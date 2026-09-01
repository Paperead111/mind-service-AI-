"""自主决策引擎：L0–L3 每轮必执行（分层 = 思考深度，不是是否调用）。

- decide()：每一轮输入必经。L0 反射（红线/锚点/冲突，零 LLM）→ L1 升层判据
  → L2 深思（驱动 + 多假设 + 预期自由能 argmin G；LLM 增强位留接口）
  → decision_log 每轮留痕（无空轮）
- after_reply()：主回复后的收束三分支 / 观点接力（规则核心 + 模板消息）
"""
import json
import uuid
from datetime import datetime, timezone

from app.cognition.boundaries import boundary_check
from app.cognition.network import neighbors
from app.db import Database, db
from app.decisions.drives import DriveSystem
from app.decisions.followup import (
    CLOSING_WORDS, DECISION_WORDS, FOLLOWUP_HOUR_LIMIT,
    continuation_score, extract_hook, hour_used, is_closing,
    recognize_state, worth_contesting,
)
from app.decisions.rebuttal import run_rebuttal
from app.decisions.refusal import check_redlines
from app.decisions.simulate import build_hypotheses, pick_action
from app.logging_setup import get_logger, log_event
from app.memory.recall import recall
from app.principles import check_principle_conflict
from app.skills.loader import match_skill

log = get_logger("decisions")

def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class DecisionEngine:
    def __init__(self, database: Database | None = None, drives: DriveSystem | None = None,
                 persona=None, llm=None):
        self.db = database or db
        self.drives = drives or DriveSystem(self.db)
        self.persona = persona   # PersonaLayer（系统提示词 + 自检）
        self.llm = llm           # DeepSeekClient；任何话术都由它生成
        from app.life.state import GlobalCognitiveState
        self._state = GlobalCognitiveState(self.db)  # R16/R19：只读快照

    # ---------- 每轮必执行（全链路深思，无 L0 直通） ----------

    def decide(self, user_text: str, session_id: str = "local") -> dict:
        """R17 骨架：decide 异常 → 回滚到最近检查点，重试 ≤1 次。"""
        try:
            return self._decide_once(user_text, session_id)
        except Exception:
            log.exception("decide 异常（第一次）：回滚到最近检查点后重试（R17）")
            try:
                from app.life.state import rollback_to_last_checkpoint
                rollback_to_last_checkpoint(self.db)
            except Exception:
                log.exception("回滚到检查点失败，仍按原状重试")
            return self._decide_once(user_text, session_id)

    def _decide_once(self, user_text: str, session_id: str = "local") -> dict:
        from app.degradation.engine import degradation
        from app.life.homeostasis import apply_turn_cost
        from app.life.stimulus import confront_due, familiarity_for
        from app.decisions.simulate import accumulate_pred_error

        # R22 快照过期重读：tick 已更新 → 生成前重读
        if self._state.is_stale():
            self._state.update_from_db()
        state_kind = recognize_state(user_text)
        red = check_redlines(user_text)
        conflicts = check_principle_conflict(user_text)
        # 上下文组装：记忆检索 + 认知网络 + 情绪 + 目标 + 边界（其他系统全部接入）
        context = self._assemble_context(user_text, state_kind)
        # 技能匹配：触发词命中 → 技能成为候选行动
        skill = match_skill(user_text)
        if skill:
            context["skill"] = skill["name"]

        # 降级冻结（盲区三）：L1b/L2 期间情绪/主观/冲突计数/p_self 事件全部冻结
        frozen = degradation.emotion_frozen()

        # R14′ 刺激痕迹：familiarity（非冲突类 R）+ CONFRONT 判定（N≥6 且 R 高）
        familiarity, trace = ((familiarity_for(user_text, conflicts, self.db))
                              if not frozen else (1.0, {"rtype": "frozen"}))
        confront = (not frozen) and confront_due(user_text, conflicts, self.db)

        # R13′ p_self 事件轮：对抗（红线/冲突）→ 立场加固；纠错 → 下调
        if not frozen:
            from app.life.self_model import apply_event
            if red or conflicts:
                apply_event(self.db, kind="confront")
            elif any(w in user_text for w in ("你错了", "不对", "说错", "纠正", "搞错了")):
                apply_event(self.db, kind="correct")

        # R15′ 认知闭合：未知领域提问 → 最高困惑边 pred_error 累积
        if (not frozen and context.get("domain_confidence") == "unknown"
                and context.get("domain") != "general"):
            accumulate_pred_error(self.db)

        # R11′ 内稳态：每轮扣减（深 −0.02 / 短 −0.005）→ 重读快照让 G 用扣后 budget
        apply_turn_cost(user_text, context.get("intensity"), self.db)
        self._state.update_from_db()
        snap = self._state.snapshot()

        # 每轮必深思：多假设 + 上下文 + 预期自由能 argmin G + 行动选择
        decision = self._deliberate(user_text, state_kind, red, conflicts, context,
                                    skill, snap, familiarity, confront)
        # 收束三分支（收束词无论哪层都要走自主判断）
        if is_closing(user_text):
            decision.update(self._closing_branch(state_kind))
        decision.update({"layer": 2, "input_type": state_kind, "context": context,
                         "familiarity": round(familiarity, 4),
                         "state_snapshot": {"budget": round(snap["budget"], 4),
                                            "p_self": round(snap["p_self"], 4),
                                            "valence": round(snap["valence"], 3),
                                            "arousal": round(snap["arousal"], 3)}})
        self._log(user_text, decision, session_id)
        return decision

    def _assemble_context(self, user_text: str, state: str) -> dict:
        """把其他系统接进来：记忆检索、认知网络、情绪、目标、认知边界。"""
        ctx = {"memory_top": [], "graph_links": [], "domain": "general",
               "domain_confidence": None, "dominant_emotion": "平静",
               "goal_top": None}
        try:
            hits = recall(user_text, k=3, database=self.db)
            ctx["memory_top"] = [h["content"][:40] for h in hits]
        except Exception:
            pass
        try:
            root = (f"knowledge:{ctx['memory_top'][0][:40]}"
                    if ctx["memory_top"] else "person:user")
            nb = neighbors(root, depth=1, database=self.db)
            ctx["graph_links"] = [f"{x['relation']}:{x['to'][:30]}"
                                  for x in nb["neighbors"][:3]]
        except Exception:
            pass
        try:
            b = boundary_check(user_text, self.db)
            ctx["domain"], ctx["domain_confidence"] = b["domain"], b["confidence"]
        except Exception:
            pass
        try:
            e = self.db.conn().execute(
                "SELECT dominant, valence FROM emotion_state WHERE id=1").fetchone()
            if e:
                ctx["dominant_emotion"], ctx["valence"] = e["dominant"], e["valence"]
        except Exception:
            pass
        try:
            g = self.db.conn().execute(
                "SELECT content FROM goals WHERE status='active'"
                " ORDER BY priority DESC LIMIT 1").fetchone()
            if g:
                ctx["goal_top"] = g["content"][:30]
        except Exception:
            pass
        return ctx

    def _needs_deliberation(self, text: str) -> bool:
        if any(w in text for w in DECISION_WORDS):
            return True
        if is_closing(text):
            return True  # 关系信号（收束/推开）→ 深思
        top = self.db.conn().execute(
            "SELECT content FROM goals WHERE status='active' ORDER BY priority DESC LIMIT 1"
        ).fetchone()
        if top and top["content"][:4] in text:
            return True
        rows = self.db.conn().execute(
            "SELECT fact FROM semantic_memories WHERE archived=0 AND confidence < 0.5"
        ).fetchall()
        if any(r["fact"][:6] in text for r in rows):
            return True
        return False

    def _deliberate(self, user_text: str, state: str, red, conflicts, context,
                    skill=None, snap: dict | None = None, familiarity: float = 1.0,
                    confront: bool = False) -> dict:
        from app.decisions.simulate import kappa_for, pick_action
        from app.life.state import save_pending
        # 拒绝决策（判定层零 LLM；话术由 compose_message 用 Key 生成）
        if red:
            return {"action": "REFUSE", "reason": f"红线：{red['desc']}",
                    "refusal": {"kind": red["kind"], "desc": red["desc"]}}
        if conflicts and "p5" in conflicts:
            return {"action": "REFUSE", "reason": "违反原则5：认知边界",
                    "refusal": {"kind": "p5", "desc": "违反原则5：认知边界"}}
        # 反驳决策（五步线）
        rebut = run_rebuttal(user_text, self.db)
        if rebut["color"] == "红":
            return {"action": "REFUSE", "reason": rebut["reason"],
                    "refusal": {"kind": "rebuttal", "desc": rebut["reason"]}}
        hyps = build_hypotheses(user_text, state)
        if rebut["color"] in ("黄", "橙"):
            return {"action": "REPLY", "rebuttal_color": rebut["color"],
                    "reason": rebut["reason"], "hypotheses": hyps}
        # 自主决策：候选行动 + 预期自由能 argmin G（每轮都走）
        if state in ("推开", "回避"):
            candidates = ["silence", "reply"]
        elif is_closing(user_text):
            candidates = ["closing", "contest"]
        else:
            candidates = ["reply"]
            if any(w in user_text for w in ("吗", "呢", "？", "?", "为什么", "怎么",
                                            "什么", "你觉得", "聊聊", "说说")):
                candidates.append("counter_ask")
            if (context.get("domain_confidence") == "unknown"
                    and context.get("domain") != "general"):
                candidates.append("lookup")
            if skill:
                candidates.append("skill")
            if confront:
                candidates.append("confront")

        # R12′ 情感门控：先剪枝再算 G；被剪困惑进 pending 议程（权重 0.7）
        pending_now = list((snap or {}).get("pending_agenda") or [])
        removed = self._gate_candidates(candidates, snap or {})
        if removed:
            pending_now.append({"topic": user_text[:30], "weight": 0.7,
                                "added_at": _now()})
            save_pending(self.db, pending_now)

        # pending 补偿：情绪恢复且议程非空 → κ=0.4 + 优先追问；本轮消费一条
        pending_active = bool(pending_now)
        if pending_active and (snap or {}).get("valence", 0) >= -0.5:
            if "counter_ask" not in candidates:
                candidates.append("counter_ask")
            pending_now = pending_now[1:]
            save_pending(self.db, pending_now)

        damping = self._damping_active()
        kappa = kappa_for(self.db, pending=pending_active, damping=damping)

        action, g, detail = pick_action(candidates, hyps, self.db,
                                        state=snap or {}, familiarity=familiarity,
                                        kappa=kappa, damping=damping)
        if action == "skill":
            return {
                "action": "SKILL", "skill_name": skill["dir"],
                "reason": f"技能触发：{skill['name']}（argmin G={round(g, 4)}）",
                "hypotheses": hyps, **detail,
                "pending_agenda": pending_now,
                "expected_value": round(self.drives.expected_value(
                    {"curiosity": 0.4, "competence": 0.8, "coherence": 0.4}), 4),
            }
        return {
            "action": action.upper(),
            "reason": f"深思：argmin G={round(g, 4)}（κ={kappa}）",
            "hypotheses": hyps, **detail,
            "pending_agenda": pending_now,
            "expected_value": round(self.drives.expected_value(
                {"curiosity": 1.0 if action in ("counter_ask", "lookup", "confront") else 0.3,
                 "competence": 0.6 if action in ("lookup", "skill") else 0.4,
                 "coherence": 0.4}), 4),
        }

    def _gate_candidates(self, candidates: list[str], snap: dict) -> list[str]:
        """R12′ 情感门控：valence<−0.5 删反问；arousal>0.8 删查证；fear>0.7 删技能；
        清空回退 REPLY。返回被剪清单。"""
        removed: list[str] = []
        if snap.get("valence", 0) < -0.5 and "counter_ask" in candidates:
            candidates.remove("counter_ask")
            removed.append("counter_ask")
        if snap.get("arousal", 0) > 0.8 and "lookup" in candidates:
            candidates.remove("lookup")
            removed.append("lookup")
        if snap.get("fear", 0) > 0.7 and "skill" in candidates:
            candidates.remove("skill")
            removed.append("skill")
        if not candidates:
            candidates.append("reply")
        if removed:
            log_event("affective_gate", removed=removed, valence=snap.get("valence"),
                      arousal=snap.get("arousal"), fear=snap.get("fear"),
                      msg=f"情感门控剪枝：{removed}")
        return removed

    def _damping_active(self) -> bool:
        """R20′ 振荡阻尼期（κ=0.1）是否生效。"""
        from app.proactive.settings import get_setting
        try:
            import time
            until = float(get_setting("oscillation_damping_until", self.db) or 0)
            return time.time() < until
        except (TypeError, ValueError):
            return False

    def _closing_branch(self, state: str) -> dict:
        """收束三分支（自主决策）：有紧要事 → 温柔争取一次；否则 → 温柔收束。
        只产出结构化参数，话术由 compose_message 用 Key 生成。"""
        if worth_contesting(self.db):
            top = self.db.conn().execute(
                "SELECT content, progress FROM goals WHERE status='active'"
                " ORDER BY priority DESC LIMIT 1"
            ).fetchone()
            hint = top["content"] if top else "这件事"
            return {"action": "CONTEST", "reason": "目标栈顶≥80%，温柔争取一次",
                    "state": state, "contest_hint": hint}
        return {"action": "CLOSING", "reason": f"收束：{state}", "state": state}

    def execute_skill(self, decision: dict, user_text: str) -> tuple[str | None, str | None]:
        """运行技能脚本：返回 (技能正文步骤, 脚本输出)。无脚本时为 (正文, None)。"""
        from app.skills.loader import get_skill, run_skill
        name = decision.get("skill_name")
        skill = get_skill(name)
        if not skill:
            return None, None
        output = run_skill(skill, self.db, user_text)
        return skill.get("body", ""), output

    # ---------- 话术生成（由自主系统调用 Key，零静态输出） ----------

    def _base_prompt(self) -> str:
        return self.persona.system_prompt() if self.persona else ""

    async def compose_message(self, decision: dict, user_text: str) -> str:
        """由自主决策系统调用 Key 生成话术：拒绝/温柔争取/温柔收束都现场组织。

        R10 零静态输出：无 Key 或生成失败一律抛 LLMError 交给降级层，
        后端不存在任何面向用户的固定兜底句。
        """
        from app.llm.client import LLMError
        action = decision["action"]
        if self.llm is None:
            raise LLMError("LLM 不可用：话术必须由主路径生成，无固定兜底句")
        if action == "REFUSE":
            desc = (decision.get("refusal") or {}).get("desc", "")
            prompt = self._base_prompt() + (
                f"\n\n现在你必须拒绝对方的请求（类别：{desc}）。红线不可商量。"
                "用你自己的话说，1-3 句：给理由，合适时给替代方向。"
                "不要客服腔，不要照抄任何现成句子，不要用'作为AI'开头。")
        elif action == "CONTEST":
            hint = decision.get("contest_hint", "这件事")
            prompt = self._base_prompt() + (
                f"\n\n对方说累了/想结束，但你判断「{hint}」就差最后一步。"
                "温柔地争取一次：先接住对方的状态，再说你的理由，最后软性请求。"
                "2-3 句，用你自己的话说，不要照抄任何现成句子。")
        elif action == "CLOSING":
            prompt = self._base_prompt() + (
                f"\n\n对方想结束对话（状态：{decision.get('state', '')}）。"
                "温柔收束：一到两句，短，用你自己的话说，不要重复常用句子。")
        else:
            raise LLMError(f"compose_message 不支持的行动：{action}")
        try:
            text = await self.llm.chat([{"role": "user", "content": prompt}],
                                       temperature=0.8)
            text = (text or "").strip()
            if len(text) >= 2:
                if self.persona and self.persona.selfcheck(text):
                    raise LLMError("自检不合格，交给降级层")
                return text
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"话术生成失败：{exc}") from exc
        raise LLMError("话术生成为空，交给降级层")

    def _log(self, user_text: str, decision: dict, session_id: str) -> None:
        record = {"hypotheses": decision.get("hypotheses", []),
                  "context": decision.get("context", {})}
        self.db.conn().execute(
            "INSERT INTO decision_log (turn_id, layer, input_type, action, reason,"
            " hypotheses, chosen_g, budget, ts) VALUES (?,?,?,?,?,?,?,?,?)",
            (f"{session_id}:{uuid.uuid4().hex[:8]}", decision["layer"],
             decision.get("input_type"), decision["action"], decision.get("reason"),
             json.dumps(record, ensure_ascii=False, default=str),
             decision.get("G"),
             (decision.get("state_snapshot") or {}).get("budget"),
             _now()),
        )
        self.db.conn().commit()
        log_event("decision", action=decision["action"],
                  G=decision.get("G"), reason=decision.get("reason"),
                  budget=(decision.get("state_snapshot") or {}).get("budget"),
                  p_self=(decision.get("state_snapshot") or {}).get("p_self"),
                  familiarity=decision.get("familiarity"),
                  msg=f"决策落库：{decision['action']}（G={decision.get('G')}）")
        if decision["action"] in ("REFUSE", "CONTEST"):
            from app.cognition.hooks import hook_decision  # 延迟导入防循环
            hook_decision(decision["action"], decision.get("reason", ""), self.db)

    # ---------- 主回复后：观点接力 ----------

    def after_reply(self, user_text: str, session_id: str = "local",
                    emotion_intensity: float | None = None) -> dict | None:
        """正常回复后是否"接一句自己的观点"。None = 不接。"""
        score = continuation_score(user_text, self.drives.state(), self.db,
                                   emotion_intensity=emotion_intensity)
        if score < 2 or hour_used(session_id, self.db) >= FOLLOWUP_HOUR_LIMIT:
            return None
        hook = extract_hook(user_text)
        self.db.conn().execute(
            "INSERT INTO followup_log (user_reply_ref, outcome, content, ts) VALUES (?,?,?,?)",
            (user_text[:60], "followup", hook, _now()),
        )
        self.db.conn().commit()
        return {"kind": "followup", "hook": hook, "score": score}
