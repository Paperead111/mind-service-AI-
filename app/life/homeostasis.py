"""内稳态（R11′）：能量预算 + 疲劳光谱 + 代谢惩罚。

- 深轮 −0.02 / 短轮 −0.005；tick +0.01/分钟当量（loop.py 回充）
- G 代谢项：λ(1−budget)·complexity，λ=0.3
- 疲劳光谱（生成参数包）：句长 18+12b / 段数 1+⌊2b⌋ / 标点 0.3+0.5b
- 写前校验：budget ∈ [0,1]，越界拒写（R17）
"""
from app.db import Database, db
from app.life.state import write_checked
from app.logging_setup import get_logger, log_event

log = get_logger("life.homeostasis")

DEEP_COST = 0.02
SHORT_COST = 0.005
TICK_RECHARGE = 0.01      # 每分钟当量
LAMBDA = 0.3

# 行动代谢复杂度（v4.1 参数总表）
COMPLEXITY = {
    "SILENCE": 0.0, "REPLY": 1.0, "COUNTER_ASK": 1.5, "LOOKUP": 2.0,
    "SKILL": 2.0, "CONTEST": 1.5, "CONFRONT": 1.5, "CLOSING": 1.0,
    "silence": 0.0, "reply": 1.0, "counter_ask": 1.5, "lookup": 2.0,
    "skill": 2.0, "contest": 1.5, "confront": 1.5, "closing": 1.0,
    "followup_view": 1.0,
}

DEEP_INTENSITY = 60   # 深轮判定：情绪强度≥60 或 消息≥40 字（与时钟一致）
DEEP_LEN = 40


def is_deep_round(user_text: str, intensity: float | None = None) -> bool:
    return ((intensity is not None and intensity >= DEEP_INTENSITY)
            or len(user_text or "") >= DEEP_LEN)


def budget(database: Database | None = None) -> float:
    row = (database or db).conn().execute(
        "SELECT budget FROM homeostatic_state WHERE id=1").fetchone()
    return float(row["budget"]) if row else 0.7


def apply_turn_cost(user_text: str, intensity: float | None,
                    database: Database | None = None) -> float:
    """每轮对话扣预算（深 −0.02 / 短 −0.005），R17 校验拒写保留上值。"""
    dbx = database or db
    cost = DEEP_COST if is_deep_round(user_text, intensity) else SHORT_COST
    before = budget(dbx)
    ok, value = write_checked(dbx, "homeostatic_state", "budget", "budget",
                              before - cost)
    log_event("budget_cost", deep=is_deep_round(user_text, intensity),
              cost=round(cost, 3), before=round(before, 4),
              after=round(value, 4), accepted=ok,
              msg=f"内稳态扣减：{'深轮' if cost == DEEP_COST else '短轮'} −{cost}")
    return value


def metabolic_term(action: str, b: float) -> float:
    """λ(1−budget)·complexity：budget 越低越偏向省力行动。"""
    return LAMBDA * (1.0 - max(0.0, min(1.0, b))) * COMPLEXITY.get(action, 1.0)


def fatigue_spectrum(b: float) -> dict:
    """疲劳光谱：随 budget 连续退行的生成参数。"""
    b = max(0.0, min(1.0, b))
    return {
        "target_sentence_length": round(18 + 12 * b, 1),
        "target_paragraphs": 1 + int(2 * b),
        "punctuation_variety": round(0.3 + 0.5 * b, 2),
    }
