# -*- coding: utf-8 -*-
"""C · 人格保真层（ash「persona fidelity layer」的 API 形态移植）。

API 摸不到 logits，退而求其次：
1. 规则预筛（零成本）：回复命中禁语/句式禁忌/失衡引号/超长句 → 疑似漂移
2. LLM 裁判（疑似才花一次调用）：按人格材料给回复打"人格偏离分" 1~5
3. 分数 < 阈值或裁定 rewrite → 带纠正指令重生成一次（每轮最多一次）
分数与修正全程留痕，长期统计反哺模型补偿表。
"""
from app.config import settings
from app.logging_setup import get_logger, log_event

log = get_logger("llm.fidelity")

JUDGE_PROMPT = (
    "你是人格保真裁判。对照人格材料，给下面的回复打「人格偏离分」：\n"
    "5=完全是她的声音；4=基本像；3=平庸但不算错；2=跑偏；1=完全不像她。\n"
    '只输出 JSON：{"score": 1-5 数字, "issues": 问题列表, "verdict": "keep"或"rewrite"}。'
)


def rule_screen(reply: str, persona) -> bool:
    """规则预筛：命中任一条 → 需要裁判。零成本。"""
    text = reply or ""
    if not text.strip():
        return True
    for g in (persona.persona.no_go or []):
        if g in text:
            return True
    for b in (persona.persona.voice.banned_sentence_patterns or []):
        if b in text:
            return True
    if text.count('"') % 2 == 1:
        return True
    import re
    for seg in re.split(r"[。！？!?；;\n，、：:]+", text):
        if len(seg.strip()) > persona.persona.voice.short_sentence_max_chars + 10:
            return True
    return False


async def judge_fidelity(reply: str, user_text: str, persona, llm) -> dict:
    """LLM 裁判：返回 {score, issues, verdict, raw}。失败时视为通过（不卡主链路）。"""
    identity = (persona.persona.identity or "")[:600]
    try:
        content = await llm.chat_json(
            [{"role": "system", "content": JUDGE_PROMPT + "\n\n人格材料：\n" + identity},
             {"role": "user", "content": f"对方的话：{user_text[:200]}\n她的回复：{reply[:500]}"}],
            temperature=0.2, max_tokens=600)
        score = float(content.get("score", 5))
        return {"score": round(min(5.0, max(1.0, score)), 1),
                "issues": content.get("issues") or [],
                "verdict": content.get("verdict", "keep")}
    except Exception as exc:
        log.warning("人格裁判失败，视为通过：%s", exc)
        return {"score": 5.0, "issues": [], "verdict": "keep"}


def needs_regeneration(judge: dict) -> bool:
    return judge["verdict"] == "rewrite" or judge["score"] < settings.fidelity_min_score


def correction_note(judge: dict) -> str:
    issues = "、".join((judge.get("issues") or [])[:3])
    return (f"[人格修正] 上一版被裁判判定偏离了她的声音（分 {judge['score']}/5"
            + (f"，问题：{issues}" if issues else "")
            + "）。重写一版：更口语、更短、更像她，别解释，只给正文。")
