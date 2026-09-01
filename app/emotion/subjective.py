"""主观系统（R2）：主观兴趣/立场/漂移。

- observe_topic：对话话题兴趣累积（+0.05/轮，钳 [0,1]）
- drift：每 tick 兴趣 ×0.97（按实际间隔折算）
- 持久化在 system_settings(subjective_state JSON)，不新增表
- 快照接口供 G 公式（familiarity×主观兴趣）与 /v1/subjective 使用
"""
import json
from datetime import datetime, timezone

from app.db import Database, db
from app.logging_setup import get_logger, log_event
from app.proactive.settings import get_setting, set_setting

log = get_logger("subjective")

INTEREST_STEP = 0.05
DRIFT_PER_TICK = 0.97
CLAMP = (0.0, 1.0)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load(database: Database | None = None) -> dict:
    raw = get_setting("subjective_state", database)
    try:
        data = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        data = {}
    return {"interests": data.get("interests", {}), "stances": data.get("stances", {})}


def save(data: dict, database: Database | None = None) -> None:
    set_setting("subjective_state", json.dumps(data, ensure_ascii=False), database)


def observe_topic(topic: str | None, database: Database | None = None) -> None:
    """一轮对话后：该话题兴趣 +0.05。"""
    if not topic:
        return
    dbx = database or db
    data = load(dbx)
    interests = data.setdefault("interests", {})
    cur = float(interests.get(topic, {}).get("interest", 0.3))
    interests[topic] = {"interest": round(min(1.0, cur + INTEREST_STEP), 4),
                        "n": interests.get(topic, {}).get("n", 0) + 1,
                        "last_at": _now()}
    save(data, dbx)


def drift(elapsed_seconds: float, database: Database | None = None) -> None:
    """tick 钩子：兴趣 ×0.97^rounds 缓慢漂移（遗忘但不归零）。"""
    dbx = database or db
    data = load(dbx)
    factor = DRIFT_PER_TICK ** max(0.0, elapsed_seconds / 60.0)
    interests = data.get("interests", {})
    for topic, item in interests.items():
        item["interest"] = round(max(0.02, float(item.get("interest", 0.3)) * factor), 4)
    save(data, dbx)


def snapshot(database: Database | None = None) -> dict:
    """供 G 公式/参数包用：{top_topic, interest}。"""
    data = load(database)
    interests = data.get("interests", {})
    if not interests:
        return {"top_topic": None, "interest": 0.0}
    top = max(interests, key=lambda t: interests[t].get("interest", 0))
    return {"top_topic": top, "interest": round(float(interests[top]["interest"]), 4)}
