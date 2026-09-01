"""连续对话 · 观点接力 + 收束三分支的规则核心。

- 收束词识别 → 状态识别（疲惫/忙碌/想结束/回避）
- 观点接力打分（≥2 分才接话）
- worth_contesting：目标栈顶 ≥80% 或高优先线程 → 温柔争取
- extract_hook：从用户回复抓具体内容（引用钩子）
"""
from app.db import Database, db

CLOSING_WORDS = ("别问了", "不用了", "先这样", "睡了", "忙", "我累了", "算了", "不说了", "明天再说")
DECISION_WORDS = ("要不要", "我想", "我考虑", "打算", "试试")
PUSH_AWAY_WORDS = ("不想说", "没事了", "没什么", "算了")

STATE_MAP = [
    (("睡了", "我累了", "困"), "疲惫"),
    (("忙", "在忙", "加班"), "忙碌"),
    (("先这样", "不说了", "明天再说", "别问了"), "想结束"),
    (("算了", "不想说", "没事了"), "回避"),
]

FOLLOWUP_HOUR_LIMIT = 3
CONSECUTIVE_LIMIT = 2


def is_closing(text: str) -> bool:
    return any(w in text for w in CLOSING_WORDS)


def recognize_state(text: str) -> str:
    for words, state in STATE_MAP:
        if any(w in text for w in words):
            return state
    if any(w in text for w in PUSH_AWAY_WORDS):
        return "推开"
    if not text.strip():
        return "沉默"
    return "正常"


def continuation_score(user_text: str, drives: dict, database: Database | None = None,
                       emotion_intensity: float | None = None) -> int:
    """观点接力打分表（纯规则，v2 放宽：普通投入的回复就能点燃）。"""
    score = 0
    if len(user_text) >= 8 or any(w in user_text for w in ("你呢", "你觉得")):
        score += 1  # 用户接话
    if len(user_text) >= 6 and any(w in user_text for w in ("为什么", "然后呢", "再讲讲", "吗", "呢")):
        score += 1  # 用户想听更多（含反问式结尾）
    if emotion_intensity is not None and emotion_intensity >= 40:
        score += 1  # 情绪在场
    if drives.get("curiosity", 0.0) >= 0.7:
        score += 1  # 好奇驱动
    conn = (database or db).conn()
    top = conn.execute(
        "SELECT content FROM goals WHERE status='active' ORDER BY priority DESC LIMIT 1"
    ).fetchone()
    if top and top["content"][:4] in user_text:
        score += 2  # 目标驱动
    if len(user_text) < 4:
        score -= 2  # 敷衍
    return score


def worth_contesting(database: Database | None = None) -> bool:
    """有没有"值得争取一下"的事：目标栈顶进度 ≥80%。"""
    conn = (database or db).conn()
    top = conn.execute(
        "SELECT content, progress FROM goals WHERE status='active'"
        " ORDER BY priority DESC LIMIT 1"
    ).fetchone()
    return bool(top and (top["progress"] or 0) >= 0.8)


def extract_hook(text: str) -> str:
    """从用户回复抓"钩子"：取信息量最大的一个分句（同长取最后一句=最新信息）。"""
    import re
    parts = [p.strip() for p in re.split(r"[。！？!?；;\n]+", text) if p.strip()]
    if not parts:
        return text.strip()
    _, best = max(enumerate(parts), key=lambda x: (len(x[1]), x[0]))
    return best


def hour_used(session_id: str, database: Database | None = None) -> int:
    conn = (database or db).conn()
    row = conn.execute(
        "SELECT COUNT(*) c FROM followup_log WHERE ts >= datetime('now','-1 hour')"
    ).fetchone()
    return row["c"]
