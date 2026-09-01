"""内部时钟（M10）：时间感知与时间-情绪耦合（v4.1 公式）。

- 孤独感 = 0.6×会话距离分量(max(0,1−近1h深轮/50)) + 离线漂移累积，钳 [0,1.5]
- 离线漂移：离线>2h → 按 0.02/小时累积（累积分量封顶 0.9，总孤独钳 1.5）
- 搁置焦虑：urgency(g)=min(1, (NOW−last_progress)/max_tolerable_idle)；anxiety=urgency×0.4
- 锚点复述：距上次锚点确认 >100 轮或 >3 天 → 提示自然复述
"""
from datetime import datetime, timedelta, timezone

from app.db import Database, db
from app.proactive.settings import get_setting, set_setting

DEEP_INTENSITY = 60       # 深度对话：情绪强度≥60 或 消息≥40 字（自我暴露）
LONELINESS_WINDOW = 50    # 会话距离分母（近 1h 深轮数 / 50）
DEEP_WINDOW_HOURS = 1.0   # v4.1：会话距离只看近 1 小时
LONELINESS_BASE_WEIGHT = 0.6   # v4.1：会话距离分量权重
OFFLINE_HOURS = 2         # 离线超过才累积
LONELINESS_RATE = 0.02    # 每离线小时累积
ACCUMULATED_CAP = 0.9     # 漂移分量封顶（0.6+0.9=1.5 不超域）
LONELINESS_CAP = 1.5
ANXIETY_WEIGHT = 0.4


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _parse_ts(s: str | None) -> datetime | None:
    """兼容两种 ts 格式：ISO（含时区）与 SQLite datetime('now','localtime')。"""
    s = (s or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_now().tzinfo)
    return dt


def deep_rounds_recent(database: Database | None = None,
                       hours: float = DEEP_WINDOW_HOURS) -> int:
    """近 N 小时内的深度对话轮数（v4.1：会话距离用时间窗，不用最近 50 条）。"""
    conn = (database or db).conn()
    cutoff = _now() - timedelta(hours=hours)
    rows = conn.execute(
        "SELECT content, intensity, ts FROM conversations WHERE role='user'"
        " ORDER BY ts DESC LIMIT 200").fetchall()
    count = 0
    for r in rows:
        dt = _parse_ts(r["ts"])
        if dt is None or dt < cutoff:
            continue
        if ((r["intensity"] is not None and r["intensity"] >= DEEP_INTENSITY)
                or (r["content"] and len(r["content"]) >= 40)):
            count += 1
    return count


def loneliness(database: Database | None = None) -> dict:
    """孤独感（v4.1）= 0.6×会话距离 + 离线漂移累积，钳 [0, 1.5]。"""
    deep = deep_rounds_recent(database)
    base = LONELINESS_BASE_WEIGHT * max(0.0, 1.0 - deep / LONELINESS_WINDOW)
    try:
        acc = float(get_setting("loneliness_accumulated", database) or 0.0)
    except ValueError:
        acc = 0.0
    total = min(LONELINESS_CAP, base + acc)
    return {"base": round(base, 3), "accumulated": round(acc, 3),
            "total": round(total, 3), "deep_rounds_recent": deep}


def accumulate_offline(database: Database | None = None) -> dict:
    """离线>2 小时 → 孤独漂移累积 += 小时×0.02（tick 调用；分量封顶 0.9）。"""
    last = get_setting("last_user_message_at", database)
    if not last:
        return {"accumulated": 0.0}
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return {"accumulated": 0.0}
    hours = (_now() - last_dt).total_seconds() / 3600
    if hours <= OFFLINE_HOURS:
        return {"accumulated": 0.0}
    gain = round((hours - OFFLINE_HOURS) * LONELINESS_RATE, 3)
    try:
        cur = float(get_setting("loneliness_accumulated", database) or 0.0)
    except ValueError:
        cur = 0.0
    new_total = round(min(ACCUMULATED_CAP, cur + gain), 3)
    set_setting("loneliness_accumulated", str(new_total), database)
    return {"accumulated": new_total, "gained": gain}


def goal_anxiety(database: Database | None = None) -> list[dict]:
    """搁置焦虑：各活跃目标的 urgency×0.4。"""
    conn = (database or db).conn()
    rows = conn.execute(
        "SELECT content, priority, progress, max_tolerable_idle_hours, last_progress_at"
        " FROM goals WHERE status='active'").fetchall()
    out = []
    for r in rows:
        lp = r["last_progress_at"]
        if not lp:
            out.append({"goal": r["content"][:40], "urgency": 0.0, "anxiety": 0.0})
            continue
        try:
            lp_dt = datetime.fromisoformat(lp)
        except ValueError:
            out.append({"goal": r["content"][:40], "urgency": 0.0, "anxiety": 0.0})
            continue
        idle_h = (_now() - lp_dt).total_seconds() / 3600
        max_idle = float(r["max_tolerable_idle_hours"] or 24.0)
        urgency = min(1.0, idle_h / max_idle)
        out.append({"goal": r["content"][:40], "urgency": round(urgency, 3),
                    "anxiety": round(urgency * ANXIETY_WEIGHT, 3)})
    return out


def anchor_recall_due(database: Database | None = None) -> dict | None:
    """距上次锚点确认 >100 轮或 >3 天 → 结构化提示（R10：reason 为枚举，UI 层自行措辞）。"""
    last = get_setting("last_anchor_at", database)
    conn = (database or db).conn()
    if not last:
        return {"due": True, "reason": "never_confirmed"}
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return None
    if (_now() - last_dt) > timedelta(days=3):
        return {"due": True, "reason": "days"}
    after = conn.execute(
        "SELECT COUNT(*) c FROM conversations WHERE role='user' AND ts > ?",
        (last,)).fetchone()["c"]
    if after > 100:
        return {"due": True, "reason": "rounds"}
    return None


def clock_summary(database: Database | None = None) -> dict:
    return {"loneliness": loneliness(database),
            "goal_anxiety": goal_anxiety(database),
            "anchor": anchor_recall_due(database)}
