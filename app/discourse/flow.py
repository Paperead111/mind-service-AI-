"""话语流层（R24）：discourse_act 状态机 + discourse_trail 意图轨迹。

- 单一话语焦点：ACKNOWLEDGE/ELABORATE/CHALLENGE/RECAST/PROJECT 五选一，
  每轮只能一个主行为，其余作支撑成分
- PROJECT 唯一合法开口：只能以"那…/所以…"开头承接前文，禁止"我也/我认为"开头
- discourse_trail：working_memory 记每轮 intent_tag（start/continue/change/close_topic）
- 碎片输入（≤4 字）默认归入最近未完成话题；短轮三选一（纯衔接/纯追问/纯回应）
- 降级轮照常以纯规则意图探测更新 trail（带 is_degraded 标记，不进决策记忆）
"""
import json
import re
import zlib
from datetime import datetime, timezone

from app.db import Database, db
from app.logging_setup import get_logger, log_event
from app.proactive.settings import get_setting, set_setting

log = get_logger("discourse")

ACTS = ("ACKNOWLEDGE", "ELABORATE", "CHALLENGE", "RECAST", "PROJECT")
SHORT_INPUT_MAX = 4          # ≤4 字 = 碎片输入
TRAIL_CAP = 20

from app.config import DATA_DIR
with (DATA_DIR / "lexicon" / "discourse.json").open(encoding="utf-8") as _f:
    DISCOURSE_LEX = json.load(_f)

TRANSITIONS = DISCOURSE_LEX["transition"]
ELABORATION_OPENINGS = DISCOURSE_LEX["elaboration_openings"]
PROJECT_OPENINGS = DISCOURSE_LEX["project_openings"]

_OPENING_RE = re.compile(r"^[，。！？\s]*[那所][么以]?[，。…]*")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _tokens(text: str) -> set:
    t = re.sub(r"[\s，。！？、；：""''（）()【】…—-]+", "", text or "")
    return {t[i:i + 2] for i in range(len(t) - 1)} | set(t)


def overlap(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


class DiscourseFlow:
    def __init__(self, database: Database | None = None):
        self.db = database or db

    # ---------- 轨迹 ----------

    def trail(self) -> list[dict]:
        try:
            return json.loads(get_setting("discourse_trail", self.db) or "[]")
        except (json.JSONDecodeError, TypeError):
            return []

    def update_trail(self, user_text: str, intent_tag: str, topic: str | None,
                     is_degraded: int = 0) -> list[dict]:
        trail = self.trail()
        trail.append({"topic": topic, "intent_tag": intent_tag,
                      "is_degraded": is_degraded, "ts": _now()})
        trail = trail[-TRAIL_CAP:]
        set_setting("discourse_trail", json.dumps(trail, ensure_ascii=False), self.db)
        return trail

    def current_topic(self) -> str | None:
        for item in reversed(self.trail()):
            if item.get("topic") and item.get("intent_tag") != "close_topic":
                return item["topic"]
        return None

    # ---------- 意图分类 ----------

    def classify_intent(self, user_text: str, trail: list[dict] | None = None) -> str:
        from app.decisions.followup import is_closing
        t = (user_text or "").strip()
        trail = trail if trail is not None else self.trail()
        if is_closing(t):
            return "close_topic"
        if len(t) <= SHORT_INPUT_MAX:
            return "continue_topic"      # 碎片默认归入最近未完成话题
        topic = (self.current_topic() or "")
        if topic and overlap(t, topic) >= 0.25:
            return "continue_topic"
        return "change_topic" if trail else "start_topic"

    # ---------- 单一话语焦点 ----------

    def choose_act(self, user_text: str, decision: dict,
                   state: dict | None = None) -> str:
        """五选一：按行动+输入形态映射；短轮强制三选一。"""
        action = decision.get("action")
        if len((user_text or "").strip()) <= SHORT_INPUT_MAX:
            return self._short_act(action)
        if action in ("REFUSE", "CONFRONT"):
            return "CHALLENGE"
        if action == "COUNTER_ASK":
            return "PROJECT"
        if action in ("LOOKUP", "SKILL"):
            return "ELABORATE"
        if action in ("CLOSING", "CONTEST"):
            return "ACKNOWLEDGE"
        if action == "SILENCE":
            return "ACKNOWLEDGE"
        if action == "REPLY":
            # 回复对方陈述 → 转述确认（RECAST）或展开（ELABORATE）
            return "RECAST" if _looks_like_share(user_text) else "ELABORATE"
        return "ELABORATE"

    def _short_act(self, action: str) -> str:
        """短轮三选一（纯衔接/纯追问/纯回应），禁回应+追问组合。"""
        if action == "COUNTER_ASK":
            return "PROJECT"      # 纯追问（回指必填）
        if action in ("CLOSING", "CONTEST", "SILENCE", "REFUSE"):
            return "ACKNOWLEDGE"  # 纯衔接/纯回应
        return "RECAST"           # 纯回应（回指必填）

    # ---------- 开口约束 ----------

    def opening_constraint(self, act: str) -> str | None:
        """PROJECT → 唯一合法开口；其余返回 None。"""
        if act == "PROJECT":
            return ("本轮只能以「那……」「所以……」开头承接前文；"
                    "禁止以「我也」「我认为」开头。")
        return None

    def pick_transition(self, text: str) -> str:
        """从词库选过渡词（按文本哈希，确定性但随内容变化）。"""
        return TRANSITIONS[zlib.crc32(text.encode()) % len(TRANSITIONS)]

    def is_elaboration_opening(self, sentence: str) -> bool:
        return any(sentence.startswith(w) for w in ELABORATION_OPENINGS)


def _looks_like_share(text: str) -> bool:
    """对方陈述型输入（分享/状态）→ 适合转述确认。"""
    return not any(w in text for w in ("吗", "呢", "？", "?", "什么", "怎么",
                                       "为什么", "帮我", "查"))


# 全局单例
discourse = DiscourseFlow()
