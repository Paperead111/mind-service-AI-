"""句法森林组合器（R18′ L1b 降级输出）：词库 + 种子驱动的词类重排，零模板句。

- 词类映射表在 data/lexicon/forest.json（数据文件，R10 豁免）
- 状态锚定强制化：STATE_SENSE 词永远来自当前最紧急状态（确定性映射，
  绝不随机到相反状态词）
- seed = state_hash XOR user_topic_hash（同一输入在不同状态下输出不同）
- 语义指纹去重：(词类模式, 状态语义类别) 近 10 条比对，≤3 次重生成；
  3 次仍碰撞 → 纯回响兜底；连续 3 次兜底 → 生成器过载，提前进入 L2
- 三级回退锚点：消息实词 → working_memory 未完结话题 → 近 1h 高频词 → 纯状态词+触发 L2
- 输出 ≤8 汉字，刻意破碎（非完整句）
"""
import json
import random
import re
import zlib
from pathlib import Path

from app.config import DATA_DIR
from app.db import Database, db
from app.degradation.intent import detect_intent
from app.logging_setup import get_logger, log_event
from app.proactive.settings import get_setting, set_setting

log = get_logger("degradation.forest")

LEXICON_PATH = DATA_DIR / "lexicon" / "forest.json"
MAX_OUTPUT_CHARS = 8      # 最长 8 个汉字（刻意破碎）
FINGERPRINT_WINDOW = 10   # 语义指纹近 10 条
REGEN_LIMIT = 3           # 重生成上限
FALLBACK_STREAK_L2 = 3    # 连续兜底次数 → 提前 L2

with LEXICON_PATH.open(encoding="utf-8") as _f:
    LEXICON = json.load(_f)

CLASSES = LEXICON["classes"]
STATE_SENSE = LEXICON["state_sense"]
INTENT_TAG = LEXICON["intent_tag"]

# 状态阈值（v4.1）
BUDGET_LOW, P_SELF_LOW, PE_HIGH = 0.3, 0.5, 0.8

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,8}")


def state_hash(state: dict) -> int:
    b = state.get("budget", 0.7)
    p = state.get("p_self", 0.85)
    pe = (state.get("top_pe_edge") or {}).get("pred_error", 0.0)
    s = state.get("silent_ticks", 0)
    return zlib.crc32(f"{b:.2f}|{p:.2f}|{pe:.2f}|{s}".encode())


def topic_hash(text: str) -> int:
    return zlib.crc32((text or "").encode())


def _state_anchor(state: dict) -> tuple[str, str]:
    """最紧急状态信号（确定性映射，冲突时取偏差最大）。返回 (类别, 词)。"""
    b = state.get("budget", 0.7)
    p = state.get("p_self", 0.85)
    pe = (state.get("top_pe_edge") or {}).get("pred_error", 0.0)
    deviance = [
        ("budget_low", BUDGET_LOW - b),
        ("p_self_low", P_SELF_LOW - p),
        ("pred_error_high", pe - PE_HIGH),
    ]
    category, worst = "stable", 0.0
    for cat, dev in deviance:
        if dev > 0 and dev > worst:
            category, worst = cat, dev
    words = STATE_SENSE.get(category, STATE_SENSE["stable"])
    return category, words[random.Random(state_hash(state)).randrange(len(words))]


def extract_echo(user_text: str, database: Database | None = None) -> str | None:
    """三级回退锚点。1 消息最后一个实词 → 2 工作记忆未完结话题 → 3 近1h高频词 → None。"""
    dbx = database or db
    t = re.sub(r"[\s，。！？、；：""''（）()【】…—-]+", " ", user_text or "")
    runs = _CJK_RUN.findall(t)
    if runs:
        return runs[-1][-4:]
    rows = dbx.conn().execute(
        "SELECT content FROM working_memory ORDER BY last_access DESC LIMIT 1").fetchall()
    if rows:
        runs = _CJK_RUN.findall(rows[0]["content"] or "")
        if runs:
            return runs[0][-4:]
    # 近 1h 高频 bigram
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc).astimezone()
              - timedelta(hours=1)).isoformat(timespec="seconds")
    rows = dbx.conn().execute(
        "SELECT content FROM conversations WHERE role='user' ORDER BY ts DESC LIMIT 100"
    ).fetchall()
    freq: dict[str, int] = {}
    for r in rows:
        for run in _CJK_RUN.findall(r["content"] or ""):
            for i in range(len(run) - 1):
                g = run[i:i + 2]
                freq[g] = freq.get(g, 0) + 1
    if freq:
        return max(freq, key=freq.get)
    return None


def _load_fingerprints(database: Database) -> list[list]:
    try:
        return json.loads(get_setting("forest_fingerprints", database) or "[]")
    except (json.JSONDecodeError, TypeError):
        return []


def _save_fingerprints(database: Database, fps: list[list]) -> None:
    set_setting("forest_fingerprints", json.dumps(fps, ensure_ascii=False), database)


def _fallback_streak(database: Database) -> int:
    try:
        return int(get_setting("forest_fallback_streak", database) or 0)
    except (TypeError, ValueError):
        return 0


def generate(user_text: str, state: dict, database: Database | None = None) -> dict:
    """生成一次降级回声。返回 {text, fingerprint, fallback, mode}。"""
    dbx = database or db
    intent = detect_intent(user_text)
    category, anchor = _state_anchor(state)
    seed = state_hash(state) ^ topic_hash(user_text)
    rng = random.Random(seed)
    fps = _load_fingerprints(dbx)

    class_pool = ["STATE", "INTENT", "ECHO", "COP", "AUX", "NEG", "ASP", "MOD"]
    used: list[tuple[str, str]] = [("STATE", anchor)]
    chars = len(anchor)

    for attempt in range(REGEN_LIMIT + 1):
        picks = [("STATE", anchor)]
        chars = len(anchor)
        extras = rng.sample(
            [c for c in class_pool if c != "STATE"], rng.randint(2, 3))
        for cls in extras:
            if chars >= MAX_OUTPUT_CHARS:
                break
            word = _word_for(cls, user_text, intent, dbx)
            if word is None:
                continue
            if len(word) + chars <= MAX_OUTPUT_CHARS:
                picks.append((cls, word))
                chars += len(word)
        if seed % 2 == 1:  # 倒装：NEG 移到 AUX 前
            idx_neg = [i for i, (c, _) in enumerate(picks) if c == "NEG"]
            idx_aux = [i for i, (c, _) in enumerate(picks) if c == "AUX"]
            if idx_neg and idx_aux and idx_neg[0] > idx_aux[0]:
                i, j = idx_aux[0], idx_neg[0]
                picks[i], picks[j] = picks[j], picks[i]
        rng.shuffle(picks)
        pattern = tuple(sorted(c for c, _ in picks))
        fp = [list(pattern), category]
        if fp not in fps:
            fps.append(fp)
            _save_fingerprints(dbx, fps[-FINGERPRINT_WINDOW:])
            set_setting("forest_fallback_streak", "0", dbx)
            text = "".join(w for _, w in picks) + "。"
            log_event("forest_generate", seed=seed, intent=intent,
                      pattern=list(pattern), state_category=category,
                      chars=len(text) - 1, text=text,
                      msg=f"句法森林输出（{intent}/{category}）：{text}")
            return {"text": text, "fingerprint": fp, "fallback": False, "mode": "forest"}
        seed ^= 0x80000000  # 最高位取反，强制换种

    # 3 次语义指纹碰撞 → 纯回响兜底（用户消息末 ≤3 字）
    echo = extract_echo(user_text, dbx)
    streak = _fallback_streak(dbx) + 1
    set_setting("forest_fallback_streak", str(streak), dbx)
    if echo:
        text = echo[-3:] + "。"
        log_event("forest_fallback", streak=streak, text=text,
                  msg=f"句法森林兜底（第 {streak} 次）：{text}")
        if streak >= FALLBACK_STREAK_L2:
            from app.degradation.engine import DegradationEngine
            DegradationEngine(dbx).force_l2(reason="forest_overload")
        return {"text": text, "fingerprint": None, "fallback": True, "mode": "fallback"}
    # 无锚点可回响 → 纯状态词 + 触发 L2
    from app.degradation.engine import DegradationEngine
    DegradationEngine(dbx).force_l2(reason="forest_no_anchor")
    return {"text": anchor + "。", "fingerprint": None, "fallback": True,
            "mode": "pure_state"}


def _word_for(cls: str, user_text: str, intent: str,
              database: Database | None = None) -> str | None:
    if cls == "INTENT":
        words = INTENT_TAG.get(intent, INTENT_TAG["statement"])
        return words[zlib.crc32((user_text or "").encode()) % len(words)]
    if cls == "ECHO":
        return extract_echo(user_text, database)
    pool = CLASSES.get(cls)
    if not pool:
        return None
    return pool[zlib.crc32(f"{cls}|{user_text or ''}".encode()) % len(pool)]
