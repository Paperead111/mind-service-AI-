"""降级意图探测（纯规则、零 LLM、盲区五闭环）。

question → [问,探,看]；positive_share → [听,接,暖]；negative_share → [接,在,沉]；
statement → [算,是,有]。
"""
import re

QUESTION_RE = re.compile(r"[？?吗呢么什么怎么为什么哪]")
POSITIVE_WORDS = ("好", "棒", "开心", "喜欢", "哈哈", "爽", "厉害", "谢谢", "爱")
NEGATIVE_WORDS = ("难过", "累", "烦", "气", "失望", "伤心", "哭", "焦虑", "害怕", "崩溃")


def detect_intent(text: str) -> str:
    t = text or ""
    if QUESTION_RE.search(t):
        return "question"
    if any(w in t for w in POSITIVE_WORDS):
        return "positive_share"
    if any(w in t for w in NEGATIVE_WORDS):
        return "negative_share"
    return "statement"
