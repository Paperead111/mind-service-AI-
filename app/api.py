"""基础 API：/v1/chat（P1 最小闭环）。

决策流水线、人格容器等在 P2–P4 接入，届时本路由改为走流水线引擎。
"""
import asyncio
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.cognition.boundaries import (answered_correctly, boundary_check,
                                      corrected, list_all)
from app.cognition.learn import (discover, mark_reviewed, review_due,
                                 run_learning)
from app.cognition.network import neighbors, stats as graph_stats
from app.config import DB_PATH, LOG_DIR, settings
from app.db import db
from app.decisions.drives import DriveSystem
from app.decisions.engine import DecisionEngine
from app.decisions.simulate import record_outcome
from app.emotion.clock import clock_summary
from app.emotion.state import EmotionSystem
from app.identity.beliefs import (confirm as belief_confirm, list_beliefs,
                                  propose as belief_propose,
                                  rollback as belief_rollback)
from app.llm.client import DeepSeekClient, LLMError
from app.logging_setup import get_logger, log_event
from app.memory.compact import weekly_compaction
from app.memory.recall import recall
from app.memory.store import MemoryStore
from app.persona.layer import PersonaLayer
from app.proactive.engine import apply_feedback
from app.proactive.settings import get_setting, set_setting
from app.service.tasks import TaskService
from app.service.ws import WSManager

TZ = ZoneInfo("Asia/Shanghai")

log = get_logger("api")

router = APIRouter(prefix="/v1")
client = DeepSeekClient()
persona = PersonaLayer()
store = MemoryStore(db)
engine = DecisionEngine(db, persona=persona, llm=client)
drives = DriveSystem(db)
emotion = EmotionSystem(db)
ws_manager = WSManager()
tasks_service = TaskService(process=None)  # 处理器在模块底部绑定（避免未定义引用）


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    history: list[dict] = []  # [{"role":"user|assistant","content":"..."}]
    session_id: str = "local"


class ChatResponse(BaseModel):
    reply: str | None = None
    model_id: str
    decision: dict = {}
    status: str = "ok"          # "ok" | "degraded"（L2 纯状态码，reply=None）


class DegradedSilent(Exception):
    """L2 静默：不生成任何文本，路由层返回纯状态码。"""


def _decision_public(decision: dict) -> dict:
    """对外暴露的决策摘要（透明思考：界面显示"她怎么想的"）。"""
    return {k: decision.get(k) for k in
            ("action", "layer", "input_type", "reason", "rebuttal_color",
             "skill_name", "G", "expected_value", "hypotheses",
             "is_degraded", "discourse_act", "cohesion_issues", "familiarity")
            if decision.get(k) is not None}


async def _compose_view(hook: str) -> str | None:
    """观点接力：基于用户的话接一句"我的观点"（LLM）。"""
    try:
        return await client.chat([
            {"role": "system",
             "content": persona.system_prompt()
             + "\n\n现在基于用户刚才的话接一句你自己的观点或感受。"
             + "不是提问（最多一个轻问句）。不超过3句。要引用他的话里的具体内容。"},
            {"role": "user", "content": f"用户刚才说：{hook}\n接一句你的观点："},
        ], temperature=0.8)
    except LLMError:
        return None


async def _chat_core(req: ChatRequest) -> tuple[str | None, dict]:
    """聊天核心链路：决策→情绪→人格→降级守卫→LLM→观点接力→落库。三路复用。

    返回 (回复文本, 决策记录)。L2 静默时抛 DegradedSilent（reply 恒为 None，
    后端不存在任何面向用户的固定兜底句——R10 零静态输出）。
    """
    import time as _time
    _t0 = _time.time()
    from app.degradation.engine import DegradedError, degradation
    from app.degradation.forest import generate as forest_generate
    from app.life.state import state as cognitive_state

    # 恢复首轮：情绪回退到降级前快照（盲区三）
    degradation.restore_emotion_if_pending()

    # 自主决策系统：每轮必执行（红线/判据/深思），全程留痕
    decision = engine.decide(req.message, req.session_id)
    # R8 判断层：意图/情绪/话题/置信度一次调用（LLM），失败回退纯规则；
    # L1b/L2 期间冻结（盲区三）——情绪只判一次（R3 由 R8 吸收）
    judged = None
    if client.configured and not degradation.emotion_frozen():
        try:
            from app.cognition.judge import judge
            judged = await judge(req.message, db, client)
        except Exception:
            judged = None
    if degradation.emotion_frozen():
        emo = emotion.perceive_frozen(req.message)
    else:
        from app.cognition.judge import judged_to_detect
        emo = emotion.perceive(req.message, detected=judged_to_detect(judged))
    store.log_conversation("user", req.message, session_id=req.session_id,
                           emotion=emo["emotion_cn"], intensity=emo["intensity"] or None)
    set_setting("last_user_message_at",
                datetime.now(TZ).isoformat(timespec="seconds"), db)
    # 驱动微调：好奇随对话逐轮升温（自主性随时间显现）
    drives.nudge({"curiosity": 0.05})

    # 常驻状态快照（R19：只读 snapshot 返回值）
    snap = cognitive_state.snapshot()
    snap["connection_reliability"] = degradation.connection_reliability()
    snap["familiarity"] = decision.get("familiarity", 1.0)     # R14′
    snap["pending_agenda"] = decision.get("pending_agenda", [])  # R12′

    # R24 话语流：单一话语焦点 + 意图轨迹（碎片输入默认归入最近未完成话题）
    from app.discourse.flow import discourse
    trail = discourse.trail()
    intent_tag = discourse.classify_intent(req.message, trail)
    decision["discourse_act"] = discourse.choose_act(req.message, decision, snap)
    snap["discourse_trail"] = trail
    new_topic = discourse.current_topic()
    if intent_tag in ("start_topic", "change_topic"):
        new_topic = re.sub(r"[\s，。！？、；：…—-]+", "", req.message)[:16] or None
    discourse.update_trail(req.message, intent_tag, new_topic, is_degraded=0)
    opening = discourse.opening_constraint(decision["discourse_act"])

    # R2 主观系统：本轮话题兴趣累积
    from app.emotion.subjective import observe_topic
    observe_topic(new_topic, db)

    # 基础温度取配置（0.7）；恢复补偿 +0.2 → 0.9，远离 1.0 的碎裂区（主路径与保真共用）
    temperature = degradation.temp_for(settings.llm_temperature)

    # 拒绝/收束/争取：判定由决策系统做出，话术由她（Key）现场生成，无固定模板
    if decision["action"] in ("REFUSE", "CONTEST", "CLOSING"):
        async def call():
            return await engine.compose_message(decision, req.message)
        try:
            reply = await degradation.guard(req.message, call)
            set_setting("last_llm_success_at",
                        datetime.now(TZ).isoformat(timespec="seconds"), db)
        except DegradedError as de:
            if de.level == "L1b":
                reply = forest_generate(req.message, snap, db)["text"]
                decision["is_degraded"] = 1
            else:
                raise DegradedSilent()
        gen_sys_prompt = persona.system_prompt()
        log.info("决策直答（她自己的话）| action=%s layer=%s",
                 decision["action"], decision["layer"])
    else:
        # ---------- B 分层加权上下文（ash weighted context 移植） ----------
        # 技能清单常驻：她知道自己会什么，也随时可以调用
        sys_skill_list = ""
        try:
            from app.skills.loader import list_skills as _ls
            skills = _ls()
            if skills:
                sys_skill_list = "\n\n## 可用技能\n" + "\n".join(
                    f"- 「{s['name']}」{s['description']}"
                    f"（触发：{'/'.join(s['triggers'][:3])}）"
                    for s in skills)
        except Exception:
            pass
        # ---------- A 检索优先生成（ash memory-first 移植） ----------
        from app.llm.retrieval import (memory_block, memory_need_judgment,
                                       retrieve_for_generation)
        need_mem, need_reason = memory_need_judgment(req.message, decision, snap, trail)
        mem_block = None
        if settings.retrieval_first_enabled and need_mem:
            hits = retrieve_for_generation(req.message, db)
            mem_block = memory_block(hits)
            decision["retrieval"] = {"needed": True, "reason": need_reason,
                                     "hits": len(hits)}
        else:
            decision["retrieval"] = {"needed": False, "reason": need_reason}
        # 上下文注入：记忆联想 / 认知网络 / 认知边界 / 目标（其他系统接入）
        ctx = decision.get("context") or {}
        extra_ctx = []
        if ctx.get("memory_top"):
            extra_ctx.append("[想起] " + "；".join(ctx["memory_top"][:2]))
        if ctx.get("graph_links"):
            extra_ctx.append("[关联] " + "；".join(ctx["graph_links"][:2]))
        if ctx.get("domain_confidence") == "unknown" and ctx.get("domain") != "general":
            extra_ctx.append(f"[边界] 对「{ctx['domain']}」把握不足——如实说不知道，不要编造")
        if ctx.get("goal_top"):
            extra_ctx.append("[目标] 正在推进：" + ctx["goal_top"])
        # 防重复：过滤碎句/失衡引号（防自我污染）
        recent = db.conn().execute(
            "SELECT content FROM conversations WHERE role='assistant'"
            " AND is_degraded=0 ORDER BY ts DESC LIMIT 8").fetchall()
        clean_recent = [r["content"][:60] for r in recent
                        if len((r["content"] or "").strip()) >= 6
                        and (r["content"] or "").count('"') % 2 == 0
                        and "，只是" not in (r["content"] or "")]
        if clean_recent:
            extra_ctx.append("[避免重复] 你最近说过这些话，不要重复原话：\n"
                             + "\n".join(f"- {c}" for c in clean_recent[:3]))
        # 情绪调制：对方情绪强度≥60 时，输出层叠加感知与语气策略
        if emo["emotion"] and (emo["intensity"] or 0) >= 60:
            extra = (f"[当前感知] 对方情绪：{emo['emotion_cn']}"
                     f"（强度 {emo['intensity']:.0f}）。"
                     f"语气调整：{emo['modulation']['expression']}。")
            if emo.get("soothe"):
                extra += f"安抚方向：{emo['soothe']}。"
            extra_ctx.append(extra)
        # 生成参数包（R10 零静态输出：只注入数值，不注入任何例句）
        from app.llm.params import build_generation_params, params_to_prompt_block
        params = build_generation_params(snap, decision, emo)
        params_block = params_to_prompt_block(params)
        # 技能行动：注入技能步骤 + 脚本结果（她的真实行动力）
        skill_hints: list[str] = []
        if decision["action"] == "SKILL":
            body, output = engine.execute_skill(decision, req.message)
            skill_hint = (f"[行动选择] 你选择了使用技能「{decision.get('skill_name', '')}」。")
            if body:
                skill_hint += f"按以下步骤执行：\n{body[:4000]}"
            if output:
                skill_hint += f"\n\n技能脚本返回：\n{output[:1500]}"
            skill_hints.append(skill_hint)
        # 组装：人格核心(1.0) > 摘要(0.3) > 检索记忆(0.7) > 近期对话(0.9) > 参数包/约束
        from app.llm.context_builder import (build_weighted_context,
                                             recent_turn_block, summary_text)
        sys_prompt = build_weighted_context(
            persona_prompt=persona.system_prompt() + sys_skill_list,
            recent_turns=recent_turn_block(db),
            memory_block_text=mem_block,
            extra_ctx=extra_ctx,
            params_block=params_block,
            opening=opening,
            skill_hints=skill_hints,
            summary_text=summary_text(db),
        )
        gen_sys_prompt = sys_prompt
        messages = [
            {"role": "system", "content": sys_prompt},
            *req.history,
            {"role": "user", "content": req.message},
        ]
        # 工具调用（正规 skill）：技能声明的工具全部注册，模型在回复过程中真实调用
        from app.skills.loader import collect_tools, dispatch_tool
        tools = collect_tools()

        if tools:
            def _execute_sync(name, args):
                return dispatch_tool(name, args)

            async def execute(name, args):
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(None, _execute_sync, name, args)

            async def call():
                return await client.chat_with_tools(
                    messages, tools, execute, temperature=temperature)
        else:
            async def call():
                return await client.chat(messages, temperature=temperature)

        try:
            reply = await degradation.guard(req.message, call)
            if not (reply or "").strip():
                log.warning("LLM 返回空回复，重试一次")
                reply = await call()
                if not (reply or "").strip():
                    raise DegradedError("L1b")
            set_setting("last_llm_success_at",
                        datetime.now(TZ).isoformat(timespec="seconds"), db)
        except DegradedError as de:
            if de.level == "L1b":
                reply = forest_generate(req.message, snap, db)["text"]
                decision["is_degraded"] = 1
            else:
                raise DegradedSilent()
        except LLMError as exc:
            # 恢复锁定期内失败只走 L1a（盲区一），这里保持原 503 语义
            log.warning("chat 失败（恢复锁定期）：%s", exc)
            raise
        # R15′ 认知闭合：回答成功 → 最高困惑边 pred_error 消减（验收 #12）
        from app.decisions.simulate import reduce_pred_error
        reduce_pred_error(decision["action"], db)
        # 观点接力：正常回复后，自主决定是否接一句自己的观点
        if client.configured and not decision.get("is_degraded"):
            follow = engine.after_reply(req.message, req.session_id,
                                        emotion_intensity=emo["intensity"] or 0)
            if follow and follow.get("kind") == "followup":
                view = await _compose_view(follow["hook"])
                if view and not persona.selfcheck(view):
                    reply = reply + "\n\n" + view

    is_degraded = 1 if decision.get("is_degraded") else 0
    if not is_degraded:
        # R24 生成后校验：回指/过渡词/指代歧义（纯规则）
        from app.llm.cohesion_check import cohesion_check
        reply, issues = cohesion_check(reply, trail, discourse.current_topic())
        decision["cohesion_issues"] = issues
        # C 人格保真：规则预筛 → LLM 裁判 → 必要时重生成一次（每轮至多一次）
        if settings.fidelity_enabled and client.configured:
            from app.llm.fidelity import (correction_note, judge_fidelity,
                                          needs_regeneration, rule_screen)
            if rule_screen(reply, persona):
                judge = await judge_fidelity(reply, req.message, persona, client)
                decision["fidelity"] = judge
                if needs_regeneration(judge):
                    try:
                        regen_messages = [
                            {"role": "system",
                             "content": gen_sys_prompt + "\n" + correction_note(judge)},
                            *req.history,
                            {"role": "user", "content": req.message},
                        ]
                        new_reply = await client.chat(regen_messages,
                                                      temperature=temperature)
                        if (new_reply or "").strip():
                            reply = new_reply.strip()
                            reply, issues2 = cohesion_check(
                                reply, trail, discourse.current_topic())
                            decision["cohesion_issues"] = issues2
                            decision["fidelity"]["regenerated"] = True
                    except Exception as exc:
                        log.warning("人格保真重生成失败，保留原回复：%s", exc)
                log_event("fidelity", **decision["fidelity"],
                          msg=(f"人格保真：{decision['fidelity'].get('score')}/5 "
                               f"{'→ 已重生成' if decision['fidelity'].get('regenerated') else '→ 保留'}"))
    store.log_conversation("assistant", reply, session_id=req.session_id,
                           is_degraded=is_degraded)
    # B 滚动摘要：每 N 轮压缩一次旧对话（best-effort，失败不阻塞主链路）
    if not is_degraded:
        try:
            from app.llm.context_builder import maybe_roll_summary
            await maybe_roll_summary(req.message, db, llm=client)
        except Exception:
            pass
    if is_degraded:
        log_event("degraded_reply", chars=len(reply or ""),
                  msg="降级回声已落库（is_degraded=1，不参与记忆召回）")
    else:
        issues = persona.selfcheck(reply)
        if issues:
            log.warning("回复自检发现问题：%s", issues)
    # 完全详细日志：每轮聊天（成功/降级）都留全量真实数据
    from app.life.state import full_snapshot
    log_event(
        "chat_done",
        action=decision.get("action"),
        G=decision.get("G"),
        discourse_act=decision.get("discourse_act"),
        familiarity=decision.get("familiarity"),
        is_degraded=is_degraded,
        reply_chars=len(reply or ""),
        latency_ms=round((_time.time() - _t0) * 1000),
        input_chars=len(req.message),
        emotion=emo.get("emotion_cn"),
        emotion_intensity=emo.get("intensity"),
        cohesion_issues=decision.get("cohesion_issues") or [],
        snapshot=full_snapshot(db),
        msg=(f"chat 完成 | 回复 {len(reply or '')} 字 | "
             f"耗时 {round((_time.time() - _t0) * 1000)}ms | "
             f"action={decision.get('action')} G={decision.get('G')} "
             f"discourse={decision.get('discourse_act')} | is_degraded={is_degraded}"),
    )
    log.info("chat 完成 | 回复 %d 字 | is_degraded=%s",
             len(reply or ""), is_degraded)
    return reply, decision


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    try:
        reply, decision = await _chat_core(req)
    except DegradedSilent:
        # L2 静默：纯状态码，无 message 字段（前端以 UI 占位符渲染）
        return ChatResponse(reply=None, model_id=client.model_id,
                            decision={}, status="degraded")
    except LLMError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return ChatResponse(reply=reply, model_id=client.model_id,
                        decision=_decision_public(decision))


async def _process_task(session_id: str, message: str, history: list) -> str:
    """异步任务处理器（走同一核心链路）。"""
    req = ChatRequest(message=message, history=history, session_id=session_id)
    try:
        reply, _ = await _chat_core(req)
        return reply or ""
    except DegradedSilent as exc:
        raise RuntimeError("degraded:L2") from exc
    except LLMError as exc:
        raise RuntimeError(str(exc)) from exc


# 绑定任务处理器（定义完成后回填）
tasks_service._process = _process_task


@router.post("/chat/async")
async def chat_async(req: ChatRequest):
    """异步聊天：立即返回 task_id，后台处理，轮询 /v1/tasks/{id} 取结果。"""
    try:
        task_id = tasks_service.submit(req.session_id, req.message, req.history)
    except asyncio.QueueFull:
        raise HTTPException(status_code=503, detail="任务队列已满，稍后再试")
    return {"task_id": task_id, "status": "pending"}


@router.get("/tasks/{task_id}")
def task_get(task_id: str):
    t = tasks_service.get(task_id)
    if t is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return t


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    """WebSocket：流式聊天 + 接收主动消息推送。"""
    await ws_manager.connect(ws)
    try:
        while True:
            data = await ws.receive_json()
            kind = data.get("type")
            if kind == "ping":
                await ws.send_json({"type": "pong"})
            elif kind == "chat":
                message = str(data.get("message", ""))[:8000]
                session_id = str(data.get("session_id", "local"))
                try:
                    reply, decision = await _chat_core(ChatRequest(
                        message=message, session_id=session_id))
                    await ws.send_json({"type": "reply", "reply": reply,
                                        "decision": _decision_public(decision)})
                except DegradedSilent:
                    await ws.send_json({"type": "degraded",
                                        "status": "degraded", "is_degraded": 1})
                except Exception as exc:
                    await ws.send_json({"type": "error",
                                        "detail": str(exc)[:300]})
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)


@router.get("/logs")
def logs_tail(lines: int = 100):
    """日志查询：返回 mind.log 最近 N 行（后台日志页同源）。"""
    f = LOG_DIR / "mind.log"
    if not f.is_file():
        return {"lines": []}
    content = f.read_text(encoding="utf-8", errors="replace").splitlines()
    return {"lines": content[-min(max(lines, 1), 2000):]}


@router.get("/persona")
async def persona_info():
    p = persona.persona
    return {
        "persona_id": p.persona_id,
        "identity_loaded": bool(p.identity),
        "voice_short_sentence_max_chars": p.voice.short_sentence_max_chars,
        "no_go_count": len(p.no_go),
        "model_tunings": list(p.tunings),
        "phrases_loaded": False,  # R10 零静态输出：phrases.md 已删除，保留字段兼容
    }


# ---------- 记忆 API（P3） ----------

class MemoryWrite(BaseModel):
    mtype: str = Field(..., pattern="^(episodic|semantic|emotional)$")
    content: str = Field(..., min_length=1)
    summary: str | None = None
    tags: list[str] = []
    importance: float = 0.5
    confidence: float = 0.5
    source: str | None = None
    emotion: str | None = None
    intensity: float | None = None


@router.post("/memory/write")
def memory_write(req: MemoryWrite):
    if req.mtype == "episodic":
        mem_id = store.add_episodic(req.content, req.summary, req.tags, req.importance)
    elif req.mtype == "semantic":
        mem_id = store.add_semantic(req.content, req.confidence, req.source)
    else:
        if req.emotion is None or req.intensity is None:
            raise HTTPException(400, "情绪记忆需要 emotion 与 intensity")
        mem_id = store.add_emotional(req.content, req.emotion, req.intensity)
    return {"id": mem_id, "mtype": req.mtype}


@router.get("/memory/search")
def memory_search(q: str, k: int = 5):
    k = min(max(k, 1), 20)
    return {"results": recall(q, k)}


@router.get("/memory/index")
def memory_index():
    rows = db.conn().execute(
        "SELECT topic, ref, promote_count FROM memory_index ORDER BY promote_count DESC"
    ).fetchall()
    return {"index": [dict(r) for r in rows]}


@router.post("/memory/compact")
def memory_compact():
    return weekly_compaction()


@router.get("/memory/stats")
def memory_stats():
    conn = db.conn()
    counts = {}
    for table in ("conversations", "episodic_memories", "semantic_memories",
                  "emotional_memories", "working_memory", "memory_index"):
        counts[table] = conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
    return {"tables": counts, "db_path": str(DB_PATH)}


# ---------- 决策 API（P4） ----------

class DecisionRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str = "local"


class OutcomeRequest(BaseModel):
    action: str
    outcome: str = Field(..., pattern="^(accepted|rejected)$")


class GoalRequest(BaseModel):
    content: str = Field(..., min_length=1)
    priority: int = 3
    progress: float = 0.0


@router.post("/decision")
def decision_run(req: DecisionRequest):
    """跑完整自主决策（L0-L2），返回决策记录。"""
    return engine.decide(req.message, req.session_id)


@router.get("/decision/log")
def decision_log_recent(k: int = 20):
    rows = db.conn().execute(
        "SELECT * FROM decision_log ORDER BY id DESC LIMIT ?", (min(max(k, 1), 100),)
    ).fetchall()
    return {"decisions": [dict(r) for r in rows]}


@router.post("/decision/outcome")
def decision_outcome(req: OutcomeRequest):
    """回合结果回写（学习闭环：动作统计影响下次 est_risk/澄清率）。"""
    record_outcome(req.action, req.outcome, db)
    return {"ok": True, "action": req.action, "outcome": req.outcome}


@router.post("/goals")
def goal_add(req: GoalRequest):
    cur = db.conn().execute(
        "INSERT INTO goals (content, priority, progress, last_progress_at)"
        " VALUES (?,?,?,?)",
        (req.content, req.priority, req.progress,
         datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")),
    )
    db.conn().commit()
    return {"id": cur.lastrowid}


@router.get("/goals")
def goal_list():
    rows = db.conn().execute(
        "SELECT * FROM goals WHERE status='active' ORDER BY priority DESC, id DESC"
    ).fetchall()
    return {"goals": [dict(r) for r in rows]}


# ---------- 主动对话 API（P5） ----------

class ReviewRequest(BaseModel):
    shadow_id: int
    verdict: str = Field(..., pattern="^(like|annoy)$")


class FeedbackRequest(BaseModel):
    trigger_type: str
    response: str = Field(..., pattern="^(positive|neutral|negative|ignored)$")


class ProactiveSettingsReq(BaseModel):
    proactive_enabled: bool | None = None
    shadow_mode: bool | None = None
    proactive_daily_budget: int | None = None
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None


@router.post("/proactive/trigger")
async def proactive_trigger(request: Request):
    """手动触发一次心跳（走完整 9 步决策链）。"""
    return await request.app.state.scheduler.run_once()


@router.get("/proactive/shadow")
def proactive_shadow(k: int = 20):
    rows = db.conn().execute(
        "SELECT * FROM shadow_log ORDER BY id DESC LIMIT ?", (min(max(k, 1), 100),)
    ).fetchall()
    return {"shadow": [dict(r) for r in rows]}


@router.get("/proactive/sent")
def proactive_sent(k: int = 20):
    rows = db.conn().execute(
        "SELECT * FROM proactive_sent ORDER BY id DESC LIMIT ?", (min(max(k, 1), 100),)
    ).fetchall()
    return {"sent": [dict(r) for r in rows]}


@router.post("/proactive/review")
def proactive_review(req: ReviewRequest):
    db.conn().execute(
        "UPDATE shadow_log SET review=? WHERE id=?", (req.verdict, req.shadow_id)
    )
    db.conn().commit()
    return {"ok": True, "shadow_id": req.shadow_id, "verdict": req.verdict}


@router.post("/proactive/feedback")
def proactive_feedback(req: FeedbackRequest):
    return apply_feedback(req.trigger_type, req.response, db)


@router.get("/proactive/settings")
def proactive_settings_get():
    keys = ("proactive_enabled", "shadow_mode", "proactive_daily_budget",
            "quiet_hours_start", "quiet_hours_end", "heartbeat_interval_seconds")
    return {k: get_setting(k, db) for k in keys}


@router.post("/proactive/settings")
def proactive_settings_post(req: ProactiveSettingsReq):
    mapping = {
        "proactive_enabled": req.proactive_enabled,
        "shadow_mode": req.shadow_mode,
        "proactive_daily_budget": req.proactive_daily_budget,
        "quiet_hours_start": req.quiet_hours_start,
        "quiet_hours_end": req.quiet_hours_end,
    }
    for k, v in mapping.items():
        if v is not None:
            set_setting(k, str(v).lower() if isinstance(v, bool) else str(v), db)
    return proactive_settings_get()


# ---------- 认知网络 / 边界 / 学习 API（P6） ----------

class BoundaryFeedbackReq(BaseModel):
    domain: str = Field(..., min_length=1)
    outcome: str = Field(..., pattern="^(correct|incorrect)$")
    correct_version: str | None = None


class LearnRunReq(BaseModel):
    topic: str = Field(..., min_length=1)


class ReviewReq(BaseModel):
    name: str = Field(..., min_length=1)


@router.get("/graph/stats")
def graph_stats_route():
    return graph_stats(db)


@router.get("/graph/neighbors")
def graph_neighbors(name: str, depth: int = 2):
    return neighbors(name, min(max(depth, 1), 3), db)


@router.get("/boundaries")
def boundaries_list():
    return {"boundaries": list_all(db)}


@router.post("/boundaries/check")
def boundaries_check(req: DecisionRequest):
    return boundary_check(req.message, db)


@router.post("/boundaries/feedback")
def boundaries_feedback(req: BoundaryFeedbackReq):
    if req.outcome == "correct":
        answered_correctly(req.domain, db)
    else:
        corrected(req.domain, req.correct_version or "", db)
    return {"ok": True, "domain": req.domain}


@router.get("/learn/queue")
def learn_queue_list():
    rows = db.conn().execute(
        "SELECT * FROM learn_queue ORDER BY id DESC LIMIT 50").fetchall()
    return {"queue": [dict(r) for r in rows]}


@router.post("/learn/discover")
def learn_discover():
    return {"discovered": discover(db)}


@router.post("/learn/run")
async def learn_run(req: LearnRunReq):
    """跑七步学习闭环（双源交叉验证 + 入图 + 审计）。"""
    return await run_learning(req.topic, db, llm=client)


@router.get("/learn/review")
def learn_review_due():
    return {"due": review_due(database=db)}


@router.post("/learn/review")
def learn_review_mark(req: ReviewReq):
    mark_reviewed(req.name, db)
    return {"ok": True, "name": req.name}


@router.get("/audit")
def audit_recent(k: int = 30):
    rows = db.conn().execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (min(max(k, 1), 200),)
    ).fetchall()
    return {"audit": [dict(r) for r in rows]}


# ---------- 情绪与内部时钟 API（P7） ----------

class PerceiveReq(BaseModel):
    message: str = Field(..., min_length=1)


class DecayReq(BaseModel):
    rounds: int = 1


@router.get("/emotion")
def emotion_state():
    return {"state": emotion.state(), "modulation": emotion.modulation()}


@router.post("/emotion/perceive")
def emotion_perceive(req: PerceiveReq):
    return emotion.perceive(req.message)


@router.post("/emotion/decay")
def emotion_decay(req: DecayReq):
    return emotion.decay(max(1, min(req.rounds, 50)))


@router.get("/clock")
def clock_route():
    return clock_summary(db)


# ---------- 信念锚点 API（P9，防漂移核心） ----------

class BeliefProposeReq(BaseModel):
    content: str = Field(..., min_length=1)
    reason: str = ""


@router.get("/beliefs")
def beliefs_list():
    return {"beliefs": list_beliefs(database=db)}


@router.post("/beliefs/propose")
def beliefs_propose(req: BeliefProposeReq):
    """提案：进入 proposed 状态，**不生效**，等用户确认。"""
    return belief_propose(req.content, req.reason, db)


@router.post("/beliefs/{belief_id}/confirm")
def beliefs_confirm(belief_id: int):
    return belief_confirm(belief_id, db)


@router.post("/beliefs/{belief_id}/rollback")
def beliefs_rollback(belief_id: int):
    return belief_rollback(belief_id, db)


# ---------- 人格经验提案 API（E · ash 人格更新移植） ----------

@router.get("/persona/proposals")
def persona_proposals_list():
    from app.identity.persona_proposals import list_proposals
    return {"proposals": list_proposals(db)}


@router.post("/persona/proposals/{pid}/confirm")
def persona_proposal_confirm(pid: int):
    from app.identity.persona_proposals import confirm
    return confirm(pid, db, persona_layer=persona)


@router.post("/persona/proposals/{pid}/reject")
def persona_proposal_reject(pid: int):
    from app.identity.persona_proposals import reject
    return reject(pid, db)


@router.post("/persona/proposals/{pid}/rollback")
def persona_proposal_rollback(pid: int):
    from app.identity.persona_proposals import rollback
    return rollback(pid, db, persona_layer=persona)


# ---------- 技能 API ----------

@router.get("/skills")
def skills_list():
    from app.skills.loader import list_skills as _ls
    return {"skills": [{k: s.get(k) for k in
                        ("name", "dir", "description", "triggers", "has_script")}
                       for s in _ls()]}


@router.get("/skills/{name}")
def skills_get(name: str):
    from app.skills.loader import get_skill as _gs
    s = _gs(name)
    if not s:
        raise HTTPException(status_code=404, detail="技能不存在")
    return {k: s.get(k) for k in
            ("name", "dir", "description", "triggers", "body", "has_script")}


@router.post("/skills/reload")
def skills_reload():
    from app.skills.loader import list_skills as _ls
    return {"skills": len(_ls(reload=True))}


# ---------- 常驻生命系统 API（R1/R16/R20′） ----------

@router.get("/health/deep")
def health_deep_route():
    """健康自检六指标 + 振荡检测（R20′，验收 #21）。"""
    from app.life.maintenance import health_deep
    return health_deep(db)


@router.get("/life/log")
def life_log_route(k: int = 50):
    """生命日志 + 能力调用计数（验收 #16 数据源）。"""
    from app.life.state import state_version
    rows = db.conn().execute(
        "SELECT * FROM life_log ORDER BY id DESC LIMIT ?",
        (min(max(k, 1), 500),)).fetchall()
    caps = db.conn().execute("SELECT * FROM capability_usage").fetchall()
    return {"life_log": [dict(r) for r in rows],
            "capability_usage": {c["capability"]: c["count"] for c in caps},
            "state_version": state_version(db)}


@router.get("/subjective")
def subjective_route():
    """主观系统快照（R2 接入前为空结构，接口先落地）。"""
    from app.life.state import state as cognitive_state
    return {"subjective": cognitive_state.snapshot()["subjective"]}


@router.post("/state/rollback")
def state_rollback_route():
    """手动回滚到最近检查点（R17）：回写 DB 状态行 + 单例重建。"""
    from app.life.state import rollback_to_last_checkpoint, state as cognitive_state
    ok = rollback_to_last_checkpoint(db)
    cognitive_state.update_from_db()
    return {"ok": ok}
