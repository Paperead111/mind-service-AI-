# -*- coding: utf-8 -*-
"""知识库检索工具：按主题从本技能的知识库中检索相关章节。"""
from app.skills.knowledge import knowledge_lookup as _kb_lookup


def knowledge_lookup(args: dict) -> str:
    return _kb_lookup(__file__, args.get("topic", ""))
