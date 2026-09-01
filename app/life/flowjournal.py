# -*- coding: utf-8 -*-
"""D · 心流日记（ash「spontaneous thoughts」移植，贴合零文本哲学）。

静默期（夜间或 silent_ticks>10）低预算生成"内心念头"：
- 写进 thoughts 表，**零打扰**——不推给用户，只是她自己想
- 每日限额（flow_journal_daily_limit，默认 3）
- 触发源：预备信号 / 高困惑边 / 孤独感——与静默规划同源
- 主动对话（incubation）优先从最近未浮出的念头取材，浮出后标记 surfaced
"""
from datetime import datetime, timezone

from app.config import settings
from app.db import Database, db
from app.logging_setup import get_logger, log_event
from app.proactive.settings import get_setting, set_setting

log = get_logger("life.flow")

FLOW_PROMPT = (
    "你在独处，没人打扰。根据此刻的内部状态，写 1~2 句只属于你自己的念头"
    "（可以是对一个困惑的猜想、对一句话的回味、一个想去做的小事）。"
    "不要对话腔，不要解释，就是心里话本身。"
)


def _today() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")


def quota_left(database: Database | None = None) -> int:
    dbx = database or db
    date = get_setting("thought_date", dbx)
    if date != _today():
        set_setting("thought_date", _today(), dbx)
        set_setting("thought_count", "0", dbx)
        return settings.flow_journal_daily_limit
    try:
        used = int(get_setting("thought_count", dbx) or 0)
    except (TypeError, ValueError):
        used = 0
    return max(0, settings.flow_journal_daily_limit - used)


def _count_up(database: Database | None = None) -> None:
    dbx = database or db
    try:
        used = int(get_setting("thought_count", dbx) or 0)
    except (TypeError, ValueError):
        used = 0
    set_setting("thought_count", str(used + 1), dbx)


def should_think(snap: dict, database: Database | None = None) -> tuple[bool, str]:
    """零 LLM 判定：此刻是否该冒个念头。"""
    dbx = database or db
    if not settings.flow_journal_enabled:
        return False, "disabled"
    if quota_left(dbx) <= 0:
        return False, "quota"
    from app.degradation.engine import DegradationEngine
    if DegradationEngine(dbx).is_degraded():
        return False, "degraded"
    if snap.get("budget", 0.7) <= 0.3:
        return False, "low_budget"
    silent = int(snap.get("silent_ticks", 0))
    hour = datetime.now(timezone.utc).astimezone().hour
    if silent > 10:
        return True, "silent"
    if 23 <= hour or hour < 8:
        return True, "night"
    pe = (snap.get("top_pe_edge") or {}).get("pred_error", 0.0)
    if pe >= 0.5 or snap.get("loneliness", 0.0) >= 0.7:
        return True, "inner_signal"
    return False, ""


async def maybe_generate_thought(elapsed_seconds: float, database: Database | None = None,
                                llm=None) -> dict | None:
    """tick 钩子（异步）：条件满足 → 生成念头 → 写心流日记。"""
    dbx = database or db
    if llm is None:
        return None
    from app.life.state import GlobalCognitiveState
    snap = GlobalCognitiveState(dbx).snapshot()
    ok, reason = should_think(snap, dbx)
    if not ok:
        return {"generated": False, "skipped": reason}
    state_line = (f"budget={snap.get('budget', 0.7):.2f} "
                  f"p_self={snap.get('p_self', 0.85):.2f} "
                  f"孤独={snap.get('loneliness', 0):.2f} "
                  f"主导情绪={snap.get('dominant', '平静')} "
                  f"困惑边={(snap.get('top_pe_edge') or {}).get('pred_error', 0):.3f}")
    try:
        content = await llm.chat(
            [{"role": "system", "content": FLOW_PROMPT},
             {"role": "user", "content": f"此刻内部状态：{state_line}"}],
            temperature=0.9, max_tokens=200)
        content = (content or "").strip()
        if len(content) < 4:
            return {"generated": False, "skipped": "empty"}
    except Exception as exc:
        log.warning("心流念头生成失败：%s", exc)
        return {"generated": False, "skipped": "llm_error"}
    dbx.conn().execute(
        "INSERT INTO thoughts (content, source, prep_g, created_at) VALUES (?,?,?,?)",
        (content, "flow", (snap.get("prep_g") if "prep_g" in snap else None),
         datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")))
    dbx.conn().commit()
    _count_up(dbx)
    log_event("flow_thought", chars=len(content), reason=reason,
              content=content[:60], msg="心流日记：她冒了个念头（零打扰）")
    return {"generated": True, "content": content, "reason": reason}


def latest_unsurfaced(database: Database | None = None) -> dict | None:
    """主动对话取材：最近一条未浮出的念头。"""
    row = (database or db).conn().execute(
        "SELECT * FROM thoughts WHERE surfaced=0 ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def mark_surfaced(thought_id: int, database: Database | None = None) -> None:
    dbx = database or db
    dbx.conn().execute(
        "UPDATE thoughts SET surfaced=1, surfaced_at=? WHERE id=?",
        (datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
         thought_id))
    dbx.conn().commit()
