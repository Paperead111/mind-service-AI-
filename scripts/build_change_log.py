# -*- coding: utf-8 -*-
"""变更档案生成器：把每批修改的「原内容 → 修改后 → 修改点」写成桌面方案文档。

规则（用户定）：每次修改代码，都必须写一份方案文档放桌面，
格式 = 原内容详细写出 + 修改后内容补充进去 + 明确指出修改点。

用法：python scripts/build_change_log.py（工作目录 = mind-service）
输出：C:/Users/gyzzz/Desktop/方案-ash架构移植A-E-原内容与修改.md
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(r"C:\Users\gyzzz\Desktop\方案-ash架构移植A-E-原内容与修改.md")

# ============ 修改文件：原内容（修改前）→ 修改后 ============
MODIFIED = [
    {
        "file": "app/db.py",
        "summary": "SCHEMA 末尾新增 3 张表：conversation_summary（B 滚动摘要）/ thoughts（D 心流日记）/ personality_proposals（E 人格提案）。原 SCHEMA 在此处结束，修改只在末尾追加，既有 35 表不动。",
        "points": [
            "新增表均在 CREATE TABLE IF NOT EXISTS，老库启动自动建表，无迁移风险",
            "三张表对应 B/D/E 三个移植项的持久化，字段设计见下方修改后内容",
        ],
        "before": '''CREATE TABLE IF NOT EXISTS life_log_archive (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT NOT NULL,
  summary TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""''',
        "after": '''CREATE TABLE IF NOT EXISTS life_log_archive (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT NOT NULL,
  summary TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- ============ ash 架构移植（A–E）============

CREATE TABLE IF NOT EXISTS conversation_summary (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL DEFAULT 'local',
  summary TEXT NOT NULL,
  span_start TEXT,
  span_end TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS thoughts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  content TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'flow',
  prep_g REAL,
  surfaced INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  surfaced_at TEXT
);

CREATE TABLE IF NOT EXISTS personality_proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  target TEXT NOT NULL,
  field TEXT,
  current TEXT,
  proposed TEXT,
  reason TEXT,
  evidence TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL,
  decided_at TEXT
);
"""''',
    },
    {
        "file": "app/config.py",
        "summary": "Settings 末尾新增 A–E 共 11 个开关/阈值，全部可环境变量覆盖，每个移植项可独立回退。",
        "points": [
            "retrieval_first_enabled（A）／weighted_context_enabled + rolling_summary_*（B）",
            "fidelity_enabled + fidelity_min_score=3.0（C）",
            "flow_journal_enabled + flow_journal_daily_limit=3（D）",
            "persona_proposals_enabled（E）",
        ],
        "before": '''    # R1 生命循环底座（60s tick，绝对时间校准）
    life_loop_enabled: bool = True
    life_tick_seconds: float = 60.0
    silent_planning_interval_ticks: int = 5   # R16 静默决策模拟：每 5 tick
    checkpoint_interval_ticks: int = 10       # R17 检查点：每 10 tick''',
        "after": '''    # R1 生命循环底座（60s tick，绝对时间校准）
    life_loop_enabled: bool = True
    life_tick_seconds: float = 60.0
    silent_planning_interval_ticks: int = 5   # R16 静默决策模拟：每 5 tick
    checkpoint_interval_ticks: int = 10       # R17 检查点：每 10 tick

    # ash 架构移植（A–E）开关（均可独立回退）
    retrieval_first_enabled: bool = True      # A：检索优先生成
    weighted_context_enabled: bool = True     # B：分层加权上下文
    rolling_summary_enabled: bool = True      # B：滚动会话摘要
    rolling_summary_interval_turns: int = 20  # B：每 N 轮压缩一次
    fidelity_enabled: bool = True             # C：人格保真裁判
    fidelity_min_score: float = 3.0           # C：低于此分重生成
    flow_journal_enabled: bool = True         # D：心流日记
    flow_journal_daily_limit: int = 3         # D：每日念头限额
    persona_proposals_enabled: bool = True    # E：人格经验提案''',
    },
    {
        "file": "app/api.py（第 1 处）",
        "summary": "temperature 前移：原只在主路径分支内定义，人格保真（C）重生成也要用，提到分支之前统一定义。",
        "points": ["温度统一取 degradation.temp_for(settings.llm_temperature)，主路径与保真重生成共用"],
        "before": '''    # R2 主观系统：本轮话题兴趣累积
    from app.emotion.subjective import observe_topic
    observe_topic(new_topic, db)

    # 拒绝/收束/争取：判定由决策系统做出，话术由她（Key）现场生成，无固定模板
    if decision["action"] in ("REFUSE", "CONTEST", "CLOSING"):''',
        "after": '''    # R2 主观系统：本轮话题兴趣累积
    from app.emotion.subjective import observe_topic
    observe_topic(new_topic, db)

    # 基础温度取配置（0.7）；恢复补偿 +0.2 → 0.9，远离 1.0 的碎裂区（主路径与保真共用）
    temperature = degradation.temp_for(settings.llm_temperature)

    # 拒绝/收束/争取：判定由决策系统做出，话术由她（Key）现场生成，无固定模板
    if decision["action"] in ("REFUSE", "CONTEST", "CLOSING"):''',
    },
    {
        "file": "app/api.py（第 2 处，核心）",
        "summary": "主路径提示词组装整体重构：从「顺序拼接」改为 A 检索优先 + B 分层加权上下文。",
        "points": [
            "A：memory_need_judgment 判定后 retrieve_for_generation 检索，memory_block 以权重0.7 注入；决策记录新增 retrieval 字段",
            "B：build_weighted_context 按 人格1.0/摘要0.3/记忆0.7/上下文0.7/近期0.9 分层；recent_turn_block 与 summary_text 来自 context_builder",
            "防重复/情绪调制两段原来直接拼 sys_prompt，现并入 extra_ctx 由构建器统一分层",
            "skill_hints 改为列表传入；gen_sys_prompt 保存供 C 保真重生成使用",
        ],
        "before": '''    else:
        sys_prompt = persona.system_prompt()
        # 技能清单常驻：她知道自己会什么，也随时可以调用
        try:
            from app.skills.loader import list_skills as _ls
            skills = _ls()
            if skills:
                sys_prompt += "\\n\\n## 可用技能\\n" + "\\n".join(
                    f"- 「{s['name']}」{s['description']}"
                    f"（触发：{'/'.join(s['triggers'][:3])}）"
                    for s in skills)
        except Exception:
            pass
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
        if extra_ctx:
            sys_prompt += "\\n\\n[上下文]\\n" + "\\n".join(extra_ctx)
        # 生成参数包（R10 零静态输出：只注入数值，不注入任何例句）
        from app.llm.params import build_generation_params, params_to_prompt_block
        params = build_generation_params(snap, decision, emo)
        sys_prompt += "\\n\\n" + params_to_prompt_block(params)
        # R24 PROJECT 唯一合法开口（生成前硬约束）
        if opening:
            sys_prompt += "\\n[开口约束] " + opening
        # 技能行动：注入技能步骤 + 脚本结果（她的真实行动力）
        if decision["action"] == "SKILL":
            body, output = engine.execute_skill(decision, req.message)
            skill_hint = (f"\\n\\n[行动选择] 你选择了使用技能「{decision.get('skill_name', '')}」。")
            if body:
                skill_hint += f"按以下步骤执行：\\n{body[:4000]}"
            if output:
                skill_hint += f"\\n\\n技能脚本返回：\\n{output[:1500]}"
            sys_prompt += skill_hint
        # 防重复：把最近说过的话注入，避免模板感（过滤碎句/失衡引号，防自我污染）
        recent = db.conn().execute(
            "SELECT content FROM conversations WHERE role='assistant'"
            " AND is_degraded=0 ORDER BY ts DESC LIMIT 8").fetchall()
        clean_recent = [r["content"][:60] for r in recent
                        if len((r["content"] or "").strip()) >= 6
                        and (r["content"] or "").count('"') % 2 == 0
                        and "，只是" not in (r["content"] or "")]
        if clean_recent:
            sys_prompt += ("\\n\\n[避免重复] 你最近说过这些话，不要重复原话：\\n"
                           + "\\n".join(f"- {c}" for c in clean_recent[:3]))
        # 情绪调制：对方情绪强度≥60 时，输出层叠加感知与语气策略
        if emo["emotion"] and (emo["intensity"] or 0) >= 60:
            extra = (f"\\n\\n[当前感知] 对方情绪：{emo['emotion_cn']}"
                     f"（强度 {emo['intensity']:.0f}）。"
                     f"语气调整：{emo['modulation']['expression']}。")
            if emo.get("soothe"):
                extra += f"安抚方向：{emo['soothe']}。"
            sys_prompt += extra
        messages = [
            {"role": "system", "content": sys_prompt},
            *req.history,
            {"role": "user", "content": req.message},
        ]''',
        "after": '''    else:
        # ---------- B 分层加权上下文（ash weighted context 移植） ----------
        # 技能清单常驻：她知道自己会什么，也随时可以调用
        sys_skill_list = ""
        try:
            from app.skills.loader import list_skills as _ls
            skills = _ls()
            if skills:
                sys_skill_list = "\\n\\n## 可用技能\\n" + "\\n".join(
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
            extra_ctx.append("[避免重复] 你最近说过这些话，不要重复原话：\\n"
                             + "\\n".join(f"- {c}" for c in clean_recent[:3]))
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
                skill_hint += f"按以下步骤执行：\\n{body[:4000]}"
            if output:
                skill_hint += f"\\n\\n技能脚本返回：\\n{output[:1500]}"
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
        ]''',
    },
    {
        "file": "app/api.py（第 3 处）",
        "summary": "生成后校验链加入 C 人格保真：规则预筛 → LLM 裁判 → 低分重生成一次。",
        "points": [
            "rule_screen 零成本预筛（禁语/句式禁忌/失衡引号/超长句）",
            "judge_fidelity 打 1~5 分 + keep/rewrite 裁定，分数进 decision[\"fidelity\"] 与事件日志",
            "低于 fidelity_min_score 或裁定 rewrite → 带纠正指令重生成一次，重生成再过 cohesion",
            "每轮至多重生成一次；裁判失败视为通过，不卡主链路",
        ],
        "before": '''    is_degraded = 1 if decision.get("is_degraded") else 0
    if not is_degraded:
        # R24 生成后校验：回指/过渡词/指代歧义（纯规则）
        from app.llm.cohesion_check import cohesion_check
        reply, issues = cohesion_check(reply, trail, discourse.current_topic())
        decision["cohesion_issues"] = issues
    store.log_conversation("assistant", reply, session_id=req.session_id,
                           is_degraded=is_degraded)''',
        "after": '''    is_degraded = 1 if decision.get("is_degraded") else 0
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
                             "content": gen_sys_prompt + "\\n" + correction_note(judge)},
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
            pass''',
    },
    {
        "file": "app/api.py（第 4 处）",
        "summary": "新增 E 人格经验提案 4 个端点。",
        "points": ["GET /v1/persona/proposals 列表；POST …/confirm（写 voice yaml+热重载）/reject/rollback"],
        "before": '''@router.post("/beliefs/{belief_id}/rollback")
def beliefs_rollback(belief_id: int):
    return belief_rollback(belief_id, db)''',
        "after": '''@router.post("/beliefs/{belief_id}/rollback")
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
    return rollback(pid, db, persona_layer=persona)''',
    },
    {
        "file": "app/main.py",
        "summary": "生命循环新增两个异步钩子：D 心流日记 + E 人格经验提案。",
        "points": ["两个钩子都带 llm_client；每 tick 各判定一次（内部有各自的限额/触发条件）"],
        "before": '''    life_loop.register_hook("learning_scan",
                            lambda elapsed: learning_scan_tick(elapsed, db, llm_client))
    app.state.life_loop = life_loop
    await life_loop.start()''',
        "after": '''    life_loop.register_hook("learning_scan",
                            lambda elapsed: learning_scan_tick(elapsed, db, llm_client))
    # D 心流日记 + E 人格经验提案（ash 自主性/人格更新移植，异步钩子）
    from app.life.flowjournal import maybe_generate_thought
    from app.identity.persona_proposals import maybe_propose
    life_loop.register_hook("flow_journal",
                            lambda elapsed: maybe_generate_thought(elapsed, db, llm_client))
    life_loop.register_hook("persona_proposals",
                            lambda elapsed: maybe_propose(db, llm_client))
    app.state.life_loop = life_loop
    await life_loop.start()''',
    },
    {
        "file": "app/proactive/engine.py（第 1 处）",
        "summary": "incubation 触发器来源升级：优先取最近未浮出的心流念头（D），其次 latent_intention（原有）。",
        "points": ["latest_unsurfaced 命中 → 以念头为来源；否则回退原 latent 逻辑"],
        "before": '''        elif candidate["type"] == "incubation":
            # R7：来源 = 静默规划产出的 latent_intention（话题或最困惑关联）
            try:
                import json as _json
                latent = _json.loads(get_setting("latent_intentions", self.db) or "[]")
            except Exception:
                latent = []
            query = ""
            if latent:
                last = latent[-1]
                query = last.get("topic") or last.get("edge") or ""''',
        "after": '''        elif candidate["type"] == "incubation":
            # R7+D：来源优先取最近未浮出的心流念头，其次 latent_intention
            from app.life.flowjournal import latest_unsurfaced
            thought = latest_unsurfaced(self.db)
            if thought:
                query = thought["content"]
            else:
                try:
                    import json as _json
                    latent = _json.loads(get_setting("latent_intentions", self.db) or "[]")
                except Exception:
                    latent = []
                query = ""
                if latent:
                    last = latent[-1]
                    query = last.get("topic") or last.get("edge") or ""''',
    },
    {
        "file": "app/proactive/engine.py（第 2 处）",
        "summary": "incubation 消息真发成功后，把取材的念头标记为已浮出。",
        "points": ["mark_surfaced 只在发送成功且类型为 incubation 时执行"],
        "before": '''            else:
                if self._send(c, source, message, now):
                    summary["sent"] += 1
                    sent_this_round += 1
                    if self.on_send:''',
        "after": '''            else:
                if self._send(c, source, message, now):
                    summary["sent"] += 1
                    sent_this_round += 1
                    # D 心流：incubation 发出的念头标记为已浮出
                    if c["type"] == "incubation":
                        try:
                            from app.life.flowjournal import (latest_unsurfaced,
                                                             mark_surfaced)
                            th = latest_unsurfaced(self.db)
                            if th:
                                mark_surfaced(th["id"], self.db)
                        except Exception:
                            pass
                    if self.on_send:''',
    },
    {
        "file": "app/static/index.html（第 1 处）",
        "summary": "设置页新增「人格经验提案」卡片。",
        "points": ["UI 层文案，属前端豁免；按钮走 /v1/persona/proposals/{id}/{act}"],
        "before": '''      <div class="card"><h3>人格</h3><div id="personaBox" class="k">加载中…</div></div>
      <div class="card"><h3>信念锚点</h3><div id="beliefsBox" class="k">加载中…</div></div>''',
        "after": '''      <div class="card"><h3>人格</h3><div id="personaBox" class="k">加载中…</div></div>
      <div class="card"><h3>人格经验提案（她的成长申请）</h3><div id="proposalsBox" class="k">加载中…</div></div>
      <div class="card"><h3>信念锚点</h3><div id="beliefsBox" class="k">加载中…</div></div>''',
    },
    {
        "file": "app/static/index.html（第 2 处）",
        "summary": "loadTab('settings') 拉取提案列表渲染 + proposalAct 确认/拒绝/回滚函数。",
        "points": ["pending 显示 确认/拒绝；confirmed 显示 回滚"],
        "before": '''    if(name==='settings'){
      const p = await api('/v1/persona');
      $('personaBox').innerHTML = '人格包：<b>'+p.persona_id+'</b> · 禁语 '+p.no_go_count+' 条 · 模型补偿：'+(p.model_tunings.join(', ')||'无');
      const b = await api('/v1/beliefs');''',
        "after": '''    if(name==='settings'){
      const p = await api('/v1/persona');
      $('personaBox').innerHTML = '人格包：<b>'+p.persona_id+'</b> · 禁语 '+p.no_go_count+' 条 · 模型补偿：'+(p.model_tunings.join(', ')||'无');
      const prop = await api('/v1/persona/proposals');
      $('proposalsBox').innerHTML = prop.proposals.length ? prop.proposals.map(x=>`
        <div class="shadow-msg">
          <div class="meta">${x.target}（${x.status}）· ${(x.created_at||'').slice(0,16).replace('T',' ')}</div>
          <div>建议规则：${x.proposed}</div>
          <div class="k">理由：${x.reason||''}</div>
          <div style="margin-top:8px">
            ${x.status==='pending' ? `
              <button class="btn" onclick="proposalAct(${x.id},'confirm')">确认（生效）</button>
              <button class="btn ghost" onclick="proposalAct(${x.id},'reject')">拒绝</button>` : ''}
            ${x.status==='confirmed' ? `<button class="btn ghost" onclick="proposalAct(${x.id},'rollback')">回滚</button>` : ''}
          </div>
        </div>`).join('') : '<span class="k">暂无——她还没提出成长申请</span>';
      const b = await api('/v1/beliefs');''',
    },
    {
        "file": "app/skills/loader.py",
        "summary": "技能匹配升级为 Claude Code 语义：no_model 技能不自动触发、按名字显式调用；get_skill 缓存兜底。",
        "points": [
            "list_skills 新增 no_model 字段（读 front matter disable-model-invocation）",
            "match_skill：第一轮触发词匹配跳过 no_model；第二轮按「用<技能名>」或文本含技能名（≥4 字）显式点名",
            "get_skill 在缓存未热身时自动扫描（此前单独调用工具会找不到技能）",
        ],
        "before": '''def match_skill(user_text: str) -> dict | None:
    """触发词匹配：命中返回技能定义，未命中 None。"""
    for s in list_skills():
        for t in s["triggers"]:
            if t and t in user_text:
                return s
    return None''',
        "after": '''def match_skill(user_text: str) -> dict | None:
    """触发词匹配（Claude Code 语义）：no_model 技能不自动触发，只按名字显式调用。

    1. 触发词命中（no_model 技能跳过）
    2. 显式点名：「用<技能名>」或用户文本包含技能名（≥4 字名）
    """
    for s in list_skills():
        if s.get("no_model"):
            continue
        for t in s["triggers"]:
            if t and t in user_text:
                return s
    for s in list_skills():
        name = s["name"]
        if name and (f"用{name}" in user_text or f"用 {name}" in user_text
                     or (len(name) >= 4 and name in user_text)):
            return s
    return None''',
    },
    {
        "file": "app/api.py（技能安装端点）",
        "summary": "新增 /v1/skills/install 与 /v1/skills/validate：安装/校验 Claude Code 格式技能。",
        "points": [
            "install：校验→转换→安装→审计；重名 409（force 覆盖）",
            "validate：只预览转换结果，不落盘",
        ],
        "before": '''@router.post("/skills/reload")
def skills_reload():
    from app.skills.loader import list_skills as _ls
    return {"skills": len(_ls(reload=True))}''',
        "after": '''@router.post("/skills/reload")
def skills_reload():
    from app.skills.loader import list_skills as _ls
    return {"skills": len(_ls(reload=True))}


class SkillInstallReq(BaseModel):
    source_path: str = Field(..., min_length=1)
    triggers: list[str] = []
    force: bool = False


@router.post("/skills/install")
def skills_install(req: SkillInstallReq):
    """安装 Claude Code 格式技能（SKILL.md + 可选脚本）到她的技能库。"""
    from app.skills.installer import install_skill, parse_claude_skill
    try:
        parsed = parse_claude_skill(req.source_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    result = install_skill(req.source_path, triggers=req.triggers,
                           force=req.force, database=db)
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("error"))
    result["parsed"] = {"name": parsed["name"], "allowed_tools": parsed["allowed_tools"],
                        "no_model": parsed["no_model"]}
    return result


@router.post("/skills/validate")
def skills_validate(req: SkillInstallReq):
    """只校验不安装：预览 CC 格式技能的转换结果。"""
    from app.skills.installer import parse_claude_skill
    try:
        return parse_claude_skill(req.source_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))''',
    },
    {
        "file": "app/skills/knowledge.py",
        "summary": "知识库检索支持单文件形态（知识库.md，按「# 标题」分片）——GitHub 上传友好化的配套改造。",
        "points": [
            "新增 _BundleChunk 鸭子类型 + _split_bundle 分片器",
            "knowledge_lookup：目录形态优先，缺省回退单文件形态",
        ],
        "before": '''    base = Path(skill_py_path).parent
    kb = base / "知识库"
    files = sorted(kb.glob("*.md")) if kb.is_dir() else []
    if not files:
        return "（该技能没有附带知识库）"''',
        "after": '''    base = Path(skill_py_path).parent
    kb = base / "知识库"
    files = sorted(kb.glob("*.md")) if kb.is_dir() else []
    if not files:
        bundle = base / "知识库.md"
        if bundle.is_file():
            files = _split_bundle(bundle)
    if not files:
        return "（该技能没有附带知识库）"''',
    },
    {
        "file": "tests/test_ash_transplant.py",
        "summary": "心流测试时间无关化：夜间窗口判定随时间抖动，改为 mock 静默态快照。",
        "points": ["patch app.life.state.GlobalCognitiveState 返回 silent_ticks=12 的快照"],
        "before": '''        set_setting("thought_count", "0", self.db)
        llm = FakeLLM(text="要是明天不下雨就好了。")
        out = asyncio.run(maybe_generate_thought(0, self.db, llm=llm))
        self.assertTrue(out["generated"])''',
        "after": '''        set_setting("thought_count", "0", self.db)
        llm = FakeLLM(text="要是明天不下雨就好了。")
        # 时间无关化：直接给静默态快照，避免夜间窗口随时间抖动
        with unittest.mock.patch("app.life.state.GlobalCognitiveState") as GS:
            GS.return_value.snapshot.return_value = snap(silent_ticks=12)
            out = asyncio.run(maybe_generate_thought(0, self.db, llm=llm))
        self.assertTrue(out["generated"])''',
    },
]

# ============ 新建文件：从磁盘读全文 ============
NEW_FILES = [
    ("app/llm/retrieval.py", "A · 检索优先生成：memory_need_judgment（零 LLM 四规则判定）/ retrieve_for_generation / memory_block（权重0.7 记忆块）。"),
    ("app/llm/context_builder.py", "B · 分层加权上下文 + 滚动摘要：build_weighted_context 四层权重 / recent_turn_block / summary_text / summary_due / maybe_roll_summary（每 N 轮 LLM 压缩，保留 3 段）。"),
    ("app/llm/fidelity.py", "C · 人格保真：rule_screen 规则预筛 / judge_fidelity LLM 裁判（1~5 分+keep/rewrite）/ needs_regeneration / correction_note。"),
    ("app/life/flowjournal.py", "D · 心流日记：should_think 触发判定（静默/夜间/困惑/孤独+限额）/ maybe_generate_thought / latest_unsurfaced / mark_surfaced。"),
    ("app/identity/persona_proposals.py", "E · 人格经验提案：纠正词聚类→LLM 起草→pending；confirm 写 voice/base.yaml（自动备份）+热重载；reject/rollback。铁律：只动 voice/base.yaml，identity 与原则永不触及。"),
    ("tests/test_ash_transplant.py", "A–E 测试 11 条：判定规则/权重分层/摘要滚动与截断/预筛与裁判/心流限额与浮出/提案确认回滚（临时人格目录）。"),
    ("app/skills/installer.py", "Claude Code 技能格式安装器：校验 CC 格式 SKILL.md → 转成她的格式（triggers 兜底/disable-model-invocation→no_model/allowed-tools 仅在有 skill.py 时注册/幂等+审计）。"),
    ("tests/test_skill_installer.py", "安装器测试 6 条：解析校验/安装转换/幂等/工具声明/no_model 标记。"),
    ("scripts/bundle_knowledge.py", "知识库打包：每个技能的 知识库/*.md 合并为单文件 知识库.md（GitHub 上传友好，144→21 个文件）。"),
]


def main():
    parts: list[str] = []
    parts.append("# 方案 · 修改档案（原内容与修改对照）")
    parts.append("")
    parts.append("> 项目：mind-service（她） ｜ 全套测试 235 全绿")
    parts.append("> 格式（用户定）：原内容详细写出 → 修改后内容补充进去 → 明确指出修改点")
    parts.append("> 批次：① ash 架构移植 A–E（2026-08-29） ② Claude Code 技能安装器 + 知识库单文件化（2026-09-04）")
    parts.append("> 不移植项 F（自定义注意力/本地GGUF/C++重写）：API 架构物理不可及，未改代码")
    parts.append("")
    parts.append("---")
    parts.append("")
    parts.append("# 第一部分 · 修改的文件（原内容 → 修改后 → 修改点）")
    parts.append("")
    for item in MODIFIED:
        parts.append(f"## 文件：{item['file']}")
        parts.append("")
        parts.append(f"**改动概要**：{item['summary']}")
        parts.append("")
        parts.append("**修改点**：")
        for p in item["points"]:
            parts.append(f"- {p}")
        parts.append("")
        parts.append("### 原内容（修改前）")
        parts.append("")
        parts.append("```python")
        parts.append(item["before"].rstrip("\n"))
        parts.append("```")
        parts.append("")
        parts.append("### 修改后内容")
        parts.append("")
        parts.append("```python")
        parts.append(item["after"].rstrip("\n"))
        parts.append("```")
        parts.append("")
        parts.append("---")
        parts.append("")
    parts.append("# 第二部分 · 新建的文件（全文）")
    parts.append("")
    for rel, desc in NEW_FILES:
        path = ROOT / rel
        if not path.is_file():
            parts.append(f"## {rel}\n\n> ⚠ 文件不存在\n\n---\n")
            continue
        parts.append(f"## {rel}")
        parts.append("")
        parts.append(f"**说明**：{desc}（新建，无修改前版本）")
        parts.append("")
        parts.append("```python")
        parts.append(path.read_text(encoding="utf-8").rstrip("\n"))
        parts.append("```")
        parts.append("")
        parts.append("---")
        parts.append("")
    OUT.write_text("\n".join(parts), encoding="utf-8")
    print(f"OK：{OUT}（{OUT.stat().st_size/1024:.0f} KB）")


if __name__ == "__main__":
    main()
