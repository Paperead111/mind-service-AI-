# -*- coding: utf-8 -*-
"""B · 分层加权上下文构建器 + 滚动会话摘要（ash「weighted context」移植）。

API 无法改注意力权重，因此用**结构分层 + 权重标注**逼近：
- W1.0 人格核心（身份/原则/规则/禁语/补偿）——永远在场
- W0.9 近期对话（最近几轮干净发言）——高权重、自然衰减
- W0.7 检索记忆 + 目标/边界/关联（A 模块供给）
- W0.3 滚动摘要（旧对话的有损压缩）——低权重常驻，把"有效上下文"拉长

滚动摘要：每 N 轮用户消息，用 LLM 把摘要跨度之外的新对话压缩，追加进
conversation_summary（保留最近 3 段，旧的并入）。降级期间跳过。
"""
from datetime import datetime, timezone

from app.config import settings
from app.db import Database, db
from app.logging_setup import get_logger, log_event

log = get_logger("llm.context")

SUMMARY_KEEP = 3        # 滚动摘要最多保留段数
SUMMARY_MAX_CHARS = 400  # 每段摘要上限


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ---------- 加权上下文 ----------

def build_weighted_context(*, persona_prompt: str, recent_turns: list[str],
                           memory_block_text: str | None,
                           extra_ctx: list[str],
                           params_block: str, opening: str | None,
                           skill_hints: list[str], summary_text: str | None) -> str:
    """按权重分层拼装系统提示词。返回完整 sys_prompt。"""
    parts: list[str] = []
    if settings.weighted_context_enabled:
        parts.append("[权重1.0·人格核心]\n" + persona_prompt)
        if summary_text:
            parts.append("[权重0.3·会话摘要] 以下是你们较早对话的压缩记忆，"
                         "只在相关时轻轻使用：\n" + summary_text)
        if memory_block_text:
            parts.append(memory_block_text)
        if extra_ctx:
            parts.append("[权重0.7·上下文]\n" + "\n".join(extra_ctx))
        if recent_turns:
            parts.append("[权重0.9·近期对话]\n" + "\n".join(recent_turns))
    else:
        parts.append(persona_prompt)
        if memory_block_text:
            parts.append(memory_block_text.replace("[权重0.7·记忆检索] ", ""))
        if extra_ctx:
            parts.append("[上下文]\n" + "\n".join(extra_ctx))
        if recent_turns:
            parts.append("\n".join(recent_turns))
    parts.append(params_block)
    if opening:
        parts.append("[开口约束] " + opening)
    for h in skill_hints:
        parts.append(h)
    return "\n\n".join(p for p in parts if p)


def recent_turn_block(database: Database | None = None, n: int = 6) -> list[str]:
    """近期对话块：最近 n 条干净（非降级、非碎句）的双方发言，按时间正序。"""
    rows = (database or db).conn().execute(
        "SELECT role, content FROM conversations WHERE is_degraded=0"
        " AND length(content) >= 4 ORDER BY id DESC LIMIT ?", (n * 2,)).fetchall()
    clean = [f"{'你' if r['role'] == 'user' else '我'}：{r['content'][:80]}"
             for r in reversed(rows)]
    return clean[-n:]


# ---------- 滚动摘要 ----------

def summary_text(database: Database | None = None) -> str | None:
    """当前滚动摘要全文（拼接保留段）。"""
    rows = (database or db).conn().execute(
        "SELECT summary FROM conversation_summary WHERE session_id='local'"
        " ORDER BY id DESC LIMIT ?", (SUMMARY_KEEP,)).fetchall()
    if not rows:
        return None
    return "\n".join(r["summary"] for r in reversed(rows))


def summary_due(database: Database | None = None) -> bool:
    """距上次摘要是否已满 N 轮用户消息。"""
    conn = (database or db).conn()
    last = conn.execute(
        "SELECT span_end FROM conversation_summary WHERE session_id='local'"
        " ORDER BY id DESC LIMIT 1").fetchone()
    if last is None:
        since = "0"
    else:
        since = last["span_end"] or "0"
    count = conn.execute(
        "SELECT COUNT(*) c FROM conversations WHERE role='user' AND is_degraded=0"
        " AND ts > ?", (since,)).fetchone()["c"]
    return count >= settings.rolling_summary_interval_turns


async def maybe_roll_summary(user_text: str, database: Database | None = None,
                             llm=None) -> str | None:
    """每 N 轮压缩一次旧对话（best-effort：失败/降级/无 LLM 都跳过）。"""
    dbx = database or db
    if not settings.rolling_summary_enabled or llm is None:
        return None
    from app.degradation.engine import DegradationEngine
    if DegradationEngine(dbx).is_degraded():
        return None
    if not summary_due(dbx):
        return None
    conn = dbx.conn()
    last = conn.execute(
        "SELECT span_end FROM conversation_summary WHERE session_id='local'"
        " ORDER BY id DESC LIMIT 1").fetchone()
    since = last["span_end"] if last and last["span_end"] else "1970-01-01"
    rows = conn.execute(
        "SELECT role, content, ts FROM conversations WHERE is_degraded=0 AND ts > ?"
        " ORDER BY id LIMIT 80", (since,)).fetchall()
    if not rows:
        return None
    dialog = "\n".join(f"{'你' if r['role'] == 'user' else '我'}：{r['content'][:100]}"
                       for r in rows)
    try:
        new_sum = await llm.chat(
            [{"role": "system", "content":
              "把这段对话压缩成不超过 150 字的要点摘要：记住说过的事实、"
              "情绪、承诺和未完成的事。只输出摘要。"},
             {"role": "user", "content": dialog}],
            temperature=0.3, max_tokens=300)
        new_sum = (new_sum or "").strip()[:SUMMARY_MAX_CHARS]
        if not new_sum:
            return None
    except Exception as exc:
        log.warning("滚动摘要生成失败，跳过本轮：%s", exc)
        return None
    end = rows[len(rows) - 1]["ts"] if rows else None
    conn.execute(
        "INSERT INTO conversation_summary (session_id, summary, span_start, span_end,"
        " updated_at) VALUES ('local',?,?,?,?)",
        (new_sum, since, end or _now(), _now()))
    # 只保留最近 SUMMARY_KEEP 段
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM conversation_summary WHERE session_id='local'"
        " ORDER BY id DESC").fetchall()]
    for old in ids[SUMMARY_KEEP:]:
        conn.execute("DELETE FROM conversation_summary WHERE id=?", (old,))
    conn.commit()
    log_event("rolling_summary", span_end=end, chars=len(new_sum),
              msg="滚动摘要已更新（旧对话压缩，权重0.3 常驻注入）")
    return new_sum
