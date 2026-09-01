"""判断层（R8）：意图/情绪/话题/置信度一次调用（LLM），失败回退纯规则。

R3 情绪 LLM 化的判定由本层吸收：同一轮情绪只判一次（v4.1 裁定）。
"""
import json

from app.db import Database, db
from app.emotion.state import EMOTION_CN, detect
from app.logging_setup import get_logger, log_event

log = get_logger("judge")

RULES_PROMPT = (
    "一次判断，只输出 JSON：{\"intent\": 意图, \"emotion\": 情绪, "
    "\"topic\": 话题, \"confidence\": 置信度}。"
    "intent 取 start_topic/continue_topic/change_topic/close_topic/question/share/statement；"
    "emotion 取 joy/sadness/anger/fear/surprise/disgust/anticipation/trust/null；"
    "topic 不超过 8 个字；confidence 0~1。"
    "不要任何思考过程、解释或多余文字，第一行就必须是 JSON 本身。"
)


async def judge(user_text: str, database: Database | None = None, llm=None) -> dict:
    """一次调用产出四元组；LLM 不可用/失败 → 纯规则回退。"""
    dbx = database or db
    if llm is None:
        return _rules(user_text, dbx)
    try:
        content = await llm.chat_json(
            [{"role": "system", "content": RULES_PROMPT},
             {"role": "user", "content": user_text}],
            temperature=0.3, max_tokens=2000)   # 2000：思考型模型需给足余量，防截断交白卷
        out = _validate(content)
        if out:
            log_event("judge", source="llm", **out,
                      msg=f"判断层：LLM 判定 {out}")
            return out
    except Exception as exc:
        log.warning("判断层 LLM 失败，回退规则：%s", exc)
    return _rules(user_text, dbx)


def _validate(content: dict) -> dict | None:
    try:
        intent = str(content.get("intent", "")).strip()
        emotion = str(content.get("emotion", "")).strip()
        topic = str(content.get("topic", "")).strip()
        conf = float(content.get("confidence", 0.5))
    except (TypeError, ValueError, AttributeError):
        return None
    if not intent:
        return None
    if emotion not in (*EMOTION_CN.keys(), "null", "none", ""):
        emotion = "null"
    return {"intent": intent, "emotion": None if emotion in ("null", "none", "")
            else emotion, "topic": topic[:8], "confidence": round(
                min(1.0, max(0.0, conf)), 3)}


def _rules(user_text: str, database: Database | None = None) -> dict:
    """规则回退：情绪=关键词检测；意图=话语流规则；话题=文本截断。"""
    d = detect(user_text)
    from app.discourse.flow import DiscourseFlow
    flow = DiscourseFlow(database)
    intent = flow.classify_intent(user_text)
    topic = user_text.strip()[:8] or None
    return {"intent": intent, "emotion": d["emotion"], "topic": topic,
            "confidence": 0.5}


def judged_to_detect(judged: dict) -> dict | None:
    """判断层输出 → emotion.perceive 的 detected 格式（R3/R8 合并）。"""
    if not judged or not judged.get("emotion"):
        return None
    from app.emotion.state import AROUSAL, VALENCE, EMOTION_CN
    emo = judged["emotion"]
    if emo not in EMOTION_CN:
        return None
    return {"emotion": emo, "emotion_cn": EMOTION_CN[emo], "intensity": 60.0,
            "valence": VALENCE.get(emo, 0.0), "arousal": AROUSAL.get(emo, 0.5)}
