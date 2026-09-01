"""刺激痕迹（R14′）：repetition_trace 泛化 + 类别隔离。

- 类型：conflict / greeting / farewell / praise / question_pattern
- 仅完全匹配才计数（禁止子串模糊匹配）
- 习惯化 R=1/(1+0.15N)；敏感化（Δt<30min 且 N≥3）R=1+0.5e^(−Δt/2h)
- conflict 系数独立不乘算（只参与 CONFRONT 判定，不乘兴趣）
- familiarity = 非冲突类 R，进入 G 作主观兴趣乘数
- CONFRONT：N≥6 且 R 高（>1.2，即敏感化中）→ 触发对抗行动
- 降级（L1b/L2）期间冲突计数冻结
"""
import math
from datetime import datetime, timezone

from app.db import Database, db
from app.logging_setup import get_logger, log_event

log = get_logger("life.stimulus")

TYPES = ("conflict", "greeting", "farewell", "praise", "question_pattern")

ALPHA = 0.15        # 习惯化系数
BETA = 0.5          # 敏感化系数
TAU_HOURS = 2.0     # 敏感化时间常数
SENS_MIN_GAP_MIN = 30
SENS_MIN_N = 3
CONFRONT_N = 6
CONFRONT_R = 1.2

GREETING_MARKERS = ("你好", "早上好", "早安", "晚安", "在吗", "嗨", "哈喽", "晚上好")
FAREWELL_MARKERS = ("再见", "拜拜", "我去睡了", "去睡了", "回头聊", "先下了")
PRAISE_MARKERS = ("你真棒", "你真好", "真厉害", "你好聪明", "太强了")
QUESTION_WORDS = ("吗", "呢", "？", "?", "什么", "怎么", "为什么", "哪")


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _now_str() -> str:
    return _now().isoformat(timespec="seconds")


def classify(text: str, conflicts: list[str] | None = None) -> tuple[str, str]:
    """消息 → (类型, 完全匹配的模式)。conflict 由决策层冲突标记驱动。"""
    t = (text or "").strip()
    if conflicts:
        return "conflict", t[:30]
    for m in GREETING_MARKERS:
        if t == m:
            return "greeting", m
    for m in FAREWELL_MARKERS:
        if t == m:
            return "farewell", m
    for m in PRAISE_MARKERS:
        if t == m:
            return "praise", m
    if t.endswith(("？", "?")) or any(t.endswith(w) for w in ("吗", "呢")):
        return "question_pattern", t[:30]
    return "statement", ""   # statement 不计入痕迹


def record(rtype: str, pattern: str, database: Database | None = None) -> dict:
    """记录一次刺激（仅完全匹配）。返回 (rtype, N, R, sensitized)。"""
    dbx = database or db
    conn = dbx.conn()
    row = conn.execute(
        "SELECT * FROM repetition_trace WHERE rtype=? AND pattern=?",
        (rtype, pattern)).fetchone()
    now = _now()
    n = (row["count"] + 1) if row else 1
    last_at = row["last_at"] if row else None
    r = 1.0
    sensitized = False
    if row and last_at:
        try:
            dt = datetime.fromisoformat(last_at)
            gap_min = (now - dt).total_seconds() / 60.0
            if (gap_min < SENS_MIN_GAP_MIN and n >= SENS_MIN_N
                    and rtype == "conflict"):
                r = 1 + BETA * math.exp(-gap_min / 60.0 / TAU_HOURS)
                sensitized = True
        except ValueError:
            pass
    if not sensitized:
        r = 1.0 / (1.0 + ALPHA * n)
    if row:
        conn.execute(
            "UPDATE repetition_trace SET count=?, last_at=?, r=? WHERE rtype=? AND pattern=?",
            (n, _now_str(), round(r, 4), rtype, pattern))
    else:
        conn.execute(
            "INSERT INTO repetition_trace (rtype, pattern, count, last_at, r)"
            " VALUES (?,?,?,?,?)", (rtype, pattern, n, _now_str(), round(r, 4)))
    conn.commit()
    log_event("stimulus", rtype=rtype, n=n, r=round(r, 4),
              sensitized=sensitized, pattern=pattern[:20],
              msg=f"刺激痕迹：{rtype} N={n} R={round(r, 4)}")
    return {"rtype": rtype, "pattern": pattern, "N": n, "R": round(r, 4),
            "sensitized": sensitized}


def familiarity_for(user_text: str, conflicts: list[str] | None,
                    database: Database | None = None) -> tuple[float, dict]:
    """非冲突类熟悉度：返回 (R, trace)。statement/无匹配 → 1.0。"""
    rtype, pattern = classify(user_text, conflicts)
    if rtype == "statement":
        return 1.0, {"rtype": "statement"}
    trace = record(rtype, pattern, database)
    if rtype == "conflict":
        # conflict 系数独立不乘算：熟悉度保持 1.0（不乘兴趣）
        return 1.0, trace
    return trace["R"], trace


def confront_due(user_text: str, conflicts: list[str] | None,
                 database: Database | None = None) -> bool:
    """CONFRONT 判定：conflict N≥6 且 R 高（敏感化中）。"""
    if not conflicts:
        return False
    rtype, pattern = classify(user_text, conflicts)
    row = (database or db).conn().execute(
        "SELECT count, r FROM repetition_trace WHERE rtype='conflict' AND pattern=?",
        (pattern,)).fetchone()
    if row is None:
        return False
    return row["count"] >= CONFRONT_N and (row["r"] or 0) >= CONFRONT_R


def decay_weekly(database: Database | None = None) -> None:
    """R20′：痕迹每 7 天 count×0.5（防永久饱和）。"""
    dbx = database or db
    dbx.conn().execute("UPDATE repetition_trace SET count=MAX(0, count/2)")
    dbx.conn().commit()
    log_event("stimulus_decay", msg="repetition_trace 每周衰减 count×0.5")
