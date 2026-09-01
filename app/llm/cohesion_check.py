"""生成后校验器（R24，纯规则零 LLM）。

- 回指检测："也/同样/一样" 无近 2 轮真实回指对象 → 删词或整句
- 句间衔接：第 2 句无过渡词（且非补充定义）→ 句号改逗号 + 从词库插入过渡词
- 指代歧义："那个/这/其/它" 无指代 → 替换为话题名
- 禁止零衔接"A。B。"并置
"""
import re

from app.discourse.flow import (ELABORATION_OPENINGS, DiscourseFlow, TRANSITIONS)
from app.logging_setup import get_logger, log_event

log = get_logger("discourse.cohesion")

ANAPHORA_WORDS = ("也", "同样", "一样")
AMBIGUOUS_PRONOUNS = ("那个", "这个", "其", "它", "这")
SENT_SPLIT = re.compile(r"[。！？!?]")
TRANSITION_START = tuple(TRANSITIONS)


def has_antecedent(text: str, trail: list[dict]) -> bool:
    """近 2 轮轨迹里存在真实回指对象（话题名或与当前句主语重叠）。"""
    recent = [t.get("topic") for t in trail[-2:] if t.get("topic")]
    return any(t and (t in text or _overlap(t, text) > 0.15) for t in recent)


def _overlap(a: str, b: str) -> float:
    ta = {a[i:i + 2] for i in range(len(a) - 1)} | set(a)
    tb = {b[i:i + 2] for i in range(len(b) - 1)} | set(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _strip_lead_punct(s: str) -> str:
    return re.sub(r"^[\s，。！？、；：…—]+", "", s)


def cohesion_check(reply: str, trail: list[dict] | None = None,
                   topic: str | None = None) -> tuple[str, list[str]]:
    """纯规则后校验：返回 (修正后文本, 问题列表)。零 LLM。"""
    trail = trail or []
    issues: list[str] = []
    text = reply or ""
    if not text.strip():
        return text, issues

    # 0 引号防护：孤立引号剥除；含引号输出不做句级重构（避免切进引号内二次破坏）
    has_quotes = any(q in text for q in ('"', '「', '」', '『', '』'))
    if text.count('"') % 2 == 1:
        text = text.replace('"', "")
        issues.append("引号失衡：剥除孤立引号")
        has_quotes = False

    # 1 回指检测：无对象 → 删词/整句（固定短语"也就是说/同样地"豁免）
    protected: dict[str, str] = {}
    for i, phrase in enumerate(("也就是说", "就是说", "同样地")):
        token = f"\x00P{i}\x00"
        if phrase in text:
            text = text.replace(phrase, token)
            protected[token] = phrase
    for w in ANAPHORA_WORDS:
        if w in text and not has_antecedent(text, trail) and not topic:
            text = text.replace(w, "")
            issues.append(f"回指无对象：删「{w}」")
    for token, phrase in protected.items():
        text = text.replace(token, phrase)

    # 2/4 句间衔接 + 禁止零衔接并置
    sents = [s.strip() for s in SENT_SPLIT.split(text) if s.strip()]
    if has_quotes:
        # 平衡引号：把以引号开头的碎片并回上一句（防切进引号内二次破坏）
        merged: list[str] = []
        for s in sents:
            if merged and (s.startswith(('"', '」', '』'))):
                merged[-1] += "。" + s
            else:
                merged.append(s)
        sents = merged
    if len(sents) >= 2:
        second = _strip_lead_punct(sents[1])
        if second and not second.startswith(TRANSITION_START) \
                and not second.startswith(tuple(ELABORATION_OPENINGS)):
            # 句号改逗号 + 插入过渡词（从词库确定性选取）
            from app.discourse.flow import DiscourseFlow
            tw = DiscourseFlow().pick_transition(text)
            rest = "。".join(sents[2:])
            text = sents[0] + "，" + tw + second
            if rest:
                text += "。" + rest
            else:
                text += "。"
            issues.append(f"零衔接并置：插入过渡词「{tw}」+句号改逗号")

    # 3 指代歧义：无指代 → 替换为话题名（含引号输出跳过）
    if topic and not has_quotes:
        for p in AMBIGUOUS_PRONOUNS:
            if p in text and topic not in text:
                text = text.replace(p, topic)
                issues.append(f"指代歧义：「{p}」→「{topic}」")

    if issues:
        log_event("cohesion_fix", issues=issues,
                  msg=f"话语流后校验修正 {len(issues)} 处")
    return text, issues
