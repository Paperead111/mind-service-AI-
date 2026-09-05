# -*- coding: utf-8 -*-
"""A · 检索优先生成（ash「记忆参与生成」的 API 形态移植）。

API 摸不到注意力层，但可以把"生成前必查记忆"做成强制步骤：
- memory_need_judgment：零 LLM 判定本轮是否需要先查记忆
  （新话题 / 领域 unknown / 最高困惑边高 / 明确回忆词 / 话题与轨迹重叠低）
- retrieve_for_generation：查记忆，产出带权重的记忆块
"""
import re

from app.db import Database, db
from app.memory.recall import recall

RECALL_MARKERS = ("记得", "上次", "之前", "以前", "还记不记得", "想起", "你说过", "我们聊过")
TOPIC_OVERLAP_LOW = 0.15   # 与当前话题重叠低于此值 → 新话题 → 查记忆
PE_HIGH = 0.5              # 最高困惑边高于此值 → 认知缺口 → 查记忆


def memory_need_judgment(user_text: str, decision: dict, snap: dict,
                         trail: list[dict] | None = None) -> tuple[bool, str]:
    """本轮是否需要检索优先。返回 (需要, 原因)。"""
    t = (user_text or "").strip()
    if any(m in t for m in RECALL_MARKERS):
        return True, "recall_word"
    if (decision.get("context") or {}).get("domain_confidence") == "unknown" \
            and (decision.get("context") or {}).get("domain") != "general":
        return True, "unknown_domain"
    top_pe = (snap.get("top_pe_edge") or {}).get("pred_error", 0.0)
    if top_pe >= PE_HIGH:
        return True, "high_pe"
    topic = None
    for item in reversed(trail or []):
        if item.get("topic") and item.get("intent_tag") != "close_topic":
            topic = item["topic"]
            break
    if topic:
        from app.discourse.flow import overlap
        if overlap(t, topic) < TOPIC_OVERLAP_LOW and len(t) >= 6:
            return True, "new_topic"
    return False, ""


def retrieve_for_generation(user_text: str, database: Database | None = None,
                            k: int = 3) -> list[dict]:
    """检索优先的记忆块：返回 recall 命中（已按相关度排序）。"""
    return recall(user_text, k=k, database=database or db)


def memory_block(hits: list[dict]) -> str | None:
    """记忆命中 → 权重标注的提示词块（ash 分层上下文 W0.7 层）。"""
    if not hits:
        return None
    lines = [f"- [{h['kind']}] {h['content']}" for h in hits]
    return "[权重0.7·记忆检索] 以下是与你当前话题相关的记忆，优先基于它们说话：\n" \
        + "\n".join(lines)
