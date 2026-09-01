# -*- coding: utf-8 -*-
"""把 know-yourself（CC BY-NC-SA 4.0）与 ash-cpp（MIT）装进她的技能库。

- know-yourself：18 个心理学工具 + 导览，各成一个技能（SKILL.md = 主对话框架全文，
  知识库/ 保留供 knowledge_lookup 工具按需检索）
- ash-cpp：非技能库（C++ 意识优先引擎），取其 7 份架构文档做成知识参考技能
运行：python scripts/install_her_skills.py（工作目录 = mind-service）
"""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "data" / "skills"
SRC_KY = Path(r"D:\DeepSeek Harness\.tmp\her-skills\know-yourself\know-yourself-main")
SRC_ASH = Path(r"D:\DeepSeek Harness\.tmp\her-skills\ash-cpp\ash-cpp-master")

# (源目录, 技能目录名, 中文名, 一句话描述, 触发词列表, 主文件)
KY_TOOLS = [
    ("attachment-styles", "know-yourself-attachment", "依恋类型测试",
     "通过依恋类型问卷与讲解，帮你认识自己的依恋风格",
     ["测依恋类型", "依恋风格", "我是安全型吗", "依恋测试", "恐惧型依恋"], "依恋类型.md"),
    ("big-five", "know-yourself-bigfive", "大五人格",
     "大五人格（开放性/尽责性/外向性/宜人性/神经质）测试与解读",
     ["大五人格", "测大五", "五因素人格", "尽责性"], "大五人格.md"),
    ("cbt", "know-yourself-cbt", "认知行为疗法",
     "CBT 结构化的认知重构对话：识别、挑战、重构思维模式",
     ["认知行为疗法", "CBT", "认知扭曲", "自动思维", "ABC模型", "认知重构"], "认知行为疗法主对话.md"),
    ("dream-analysis", "know-yourself-dream", "梦的解析",
     "自由联想式解梦对话：探索梦境的象征与情绪线索",
     ["解梦", "我梦见", "梦到", "梦境", "帮我解个梦"], "梦的解析.md"),
    ("emotional-first-aid", "know-yourself-emotion-aid", "情绪急救",
     "九类情绪伤口的即时安抚与自我照顾指南",
     ["情绪急救", "我难受得不行", "心情崩溃了", "撑不住了", "好孤独", "很羞耻"], "emotional-first-aid.md"),
    ("enneagram", "know-yourself-enneagram", "九型人格",
     "九型人格测试与九种类型的深度解读",
     ["九型人格", "我几号", "九型测试", "完美型"], "九型人格.md"),
    ("five-love-languages", "know-yourself-lovelang", "爱的五种语言",
     "爱的五种语言测试与相处建议（肯定/服务/礼物/时刻/接触）",
     ["爱的语言", "爱语", "爱的五种语言", "爱语测试"], "爱的五种语言.md"),
    ("hypnosis", "know-yourself-hypnosis", "催眠放松引导",
     "结构化催眠与深度放松引导（艾瑞克森与传统方法）",
     ["催眠", "放松引导", "深度放松", "催眠引导"], "催眠引导.md"),
    ("intimate-relationships", "know-yourself-relationship", "亲密关系",
     "亲密关系健康评估、冲突风格与修复沟通",
     ["亲密关系", "关系健康", "怎么和对象相处", "修复关系", "关系生命周期"], "亲密关系.md"),
    ("jung-archetypes-shadow", "know-yourself-jung", "荣格原型与阴影",
     "荣格原型、阴影工作与个体化英雄之旅的探索对话",
     ["荣格", "原型", "阴影", "英雄之旅", "阴影工作"], "原型与阴影.md"),
    ("love-attitudes", "know-yourself-love-attitudes", "爱情态度",
     "爱情态度 LAS 测试：浪漫/同伴/游戏/现实/占有/奉献型",
     ["爱情态度", "爱情观测试", "我的爱情类型", "爱情态度测试"], "爱情态度.md"),
    ("MBTI", "know-yourself-mbti", "MBTI人格测试",
     "MBTI 十六型人格测试、认知功能与关系对照",
     ["MBTI", "我是什么人格", "16型人格", "测人格", "INFP", "INTJ", "ENFP", "ISTJ"], "mbti.md"),
    ("mindfulness-meditation", "know-yourself-mindfulness", "正念冥想",
     "正念冥想引导：呼吸观察/身体扫描/正念行走/慈心冥想",
     ["正念", "冥想", "身体扫描", "慈心冥想", "正念引导"], "正念冥想.md"),
    ("nlp-belief-reframing", "know-yourself-nlp", "NLP信念重塑",
     "NLP 换框法与限制性信念重塑对话",
     ["NLP", "信念重塑", "换框法", "限制性信念", "信念改写"], "NLP信念重塑.md"),
    ("PDP", "know-yourself-pdp", "PDP行为特质",
     "PDP 行为特质测试（老虎/孔雀/考拉/猫头鹰/变色龙）",
     ["PDP", "老虎型", "孔雀型", "考拉型", "猫头鹰型"], "PDP.md"),
    ("psychoanalysis", "know-yourself-psychoanalysis", "潜意识探索",
     "精神分析式潜意识探索与自由联想对话",
     ["潜意识", "精神分析", "弗洛伊德", "自由联想", "潜意识探索"], "潜意识探索.md"),
    ("self-actualization", "know-yourself-self-actualization", "自我实现",
     "马斯洛需求层次、自我实现者特征与高峰体验探索",
     ["自我实现", "马斯洛", "高峰体验", "需求层次", "罗杰斯"], "自我实现.md"),
    ("triangular-love", "know-yourself-triangular-love", "爱情三角",
     "爱情三角（激情/亲密/承诺）测试与关系诊断",
     ["爱情三角", "激情亲密承诺", "爱情三角测试"], "爱情三角.md"),
]

SKILL_PY_TEMPLATE = '''# -*- coding: utf-8 -*-
"""知识库检索工具：按主题从本技能的知识库中检索相关章节。"""
from app.skills.knowledge import knowledge_lookup as _kb_lookup


def knowledge_lookup(args: dict) -> str:
    return _kb_lookup(__file__, args.get("topic", ""))
'''


def slug_dir(d: str) -> str:
    return d


def install_know_yourself() -> None:
    tools = SRC_KY / "know-yourself-deepseek" / "tools"
    for src_dir, dst, name, desc, triggers, main in KY_TOOLS:
        out = SKILLS / dst
        out.mkdir(parents=True, exist_ok=True)
        body = (tools / src_dir / main).read_text(encoding="utf-8").strip()
        fm = ["---", f"name: {name}", f"description: {desc}", "triggers:"]
        for t in triggers:
            fm.append(f"  - {t}")
        fm += [
            "tools:",
            "  - name: knowledge_lookup",
            "    description: 从本工具的知识库中检索相关章节",
            "    parameters:",
            "      type: object",
            "      properties:",
            "        topic:",
            "          type: string",
            "          description: 要检索的主题或关键词",
            "      required: [topic]",
            "---",
        ]
        skill_md = "\n".join(fm) + "\n\n" + body + "\n"
        (out / "SKILL.md").write_text(skill_md, encoding="utf-8")
        # 知识库随包复制
        kb_src = tools / src_dir / "知识库"
        if kb_src.is_dir():
            kb_dst = out / "知识库"
            kb_dst.mkdir(exist_ok=True)
            for f in kb_src.glob("*.md"):
                (kb_dst / f.name).write_text(f.read_text(encoding="utf-8"),
                                             encoding="utf-8")
        (out / "skill.py").write_text(SKILL_PY_TEMPLATE, encoding="utf-8")
        print(f"  技能: {dst}（{name}，知识库 {len(list((out / '知识库').glob('*.md'))) if (out / '知识库').is_dir() else 0} 份）")

    # 导览技能
    out = SKILLS / "know-yourself-map"
    out.mkdir(parents=True, exist_ok=True)
    body = (tools / "map.md").read_text(encoding="utf-8").strip()
    fm = ("---\nname: 心理学工具导览\ndescription: 浏览全部心理探索工具，选一个开始\n"
          "triggers:\n  - 认识自己\n  - 心理工具\n  - 有哪些心理测评\n  - 帮我认识自己\n"
          "tools:\n  - name: knowledge_lookup\n    description: 从知识库检索相关章节\n"
          "    parameters:\n      type: object\n      properties:\n        topic:\n"
          "          type: string\n      required: [topic]\n---\n")
    (out / "SKILL.md").write_text(fm + "\n" + body + "\n", encoding="utf-8")
    (out / "skill.py").write_text(SKILL_PY_TEMPLATE, encoding="utf-8")
    kb = out / "知识库"
    kb.mkdir(exist_ok=True)
    kb_src = SRC_KY / "wiki" / "users"
    for name in ("getting-started.md", "how-to-use.md", "faq.md", "privacy.md"):
        f = kb_src / name
        if f.is_file():
            (kb / name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    (kb / "disclaimer.md").write_text(
        (SRC_KY / "wiki" / "legal" / "DISCLAIMER.md").read_text(encoding="utf-8"),
        encoding="utf-8")
    print("  技能: know-yourself-map（心理学工具导览）")
    (SKILLS / "LICENSE-know-yourself.txt").write_text(
        (SRC_KY / "LICENSE").read_text(encoding="utf-8", errors="replace"),
        encoding="utf-8")
    print("  许可证: LICENSE-know-yourself.txt（CC BY-NC-SA 4.0）")


def install_ash() -> None:
    out = SKILLS / "ash-cpp-architecture"
    out.mkdir(parents=True, exist_ok=True)
    docs = ["ARCHITECTURE.md", "ASH_ENGINE_SPEC.md", "MODEL_STRATEGY.md",
            "MODEL_LOADING.md", "MCP_INTEGRATION.md", "PERFORMANCE_BASELINE.md",
            "README.md"]
    kb = out / "知识库"
    kb.mkdir(exist_ok=True)
    for d in docs:
        f = SRC_ASH / d
        if f.is_file():
            (kb / d).write_text(f.read_text(encoding="utf-8", errors="replace"),
                                encoding="utf-8")
    fm = ("---\nname: 意识优先架构参考\ndescription: ash-cpp 引擎的意识优先架构文档"
          "（情绪/记忆/决策先于推理），供讨论与借鉴\n"
          "triggers:\n  - ash 引擎\n  - 意识优先\n  - ash-cpp\n  - 情绪先于推理\n"
          "  - 自主 AI 架构\n"
          "tools:\n  - name: knowledge_lookup\n    description: 从架构文档中检索相关章节\n"
          "    parameters:\n      type: object\n      properties:\n        topic:\n"
          "          type: string\n      required: [topic]\n---\n")
    body = ("# 意识优先架构参考\n\n"
            "这是 ash-cpp（Autonomous AI agent with consciousness-first architecture）"
            "的架构文档合集：情绪、记忆、决策在推理之前构建。\n"
            "涉及主题：决策引擎 / 情绪状态 / 记忆存储 / persona / 事件循环 / GGUF 模型加载"
            " / MCP 集成 / 性能基线。\n"
            "用 knowledge_lookup 检索具体章节，讨论时只作参考，不为实现背书。\n")
    (out / "SKILL.md").write_text(fm + "\n" + body, encoding="utf-8")
    (out / "skill.py").write_text(SKILL_PY_TEMPLATE, encoding="utf-8")
    (SKILLS / "LICENSE-ash-cpp.txt").write_text(
        (SRC_ASH / "LICENSE").read_text(encoding="utf-8", errors="replace"),
        encoding="utf-8")
    print(f"  技能: ash-cpp-architecture（知识库 {len(docs)} 份）")
    print("  许可证: LICENSE-ash-cpp.txt（MIT）")


if __name__ == "__main__":
    print("== 安装 know-yourself ==")
    install_know_yourself()
    print("== 安装 ash-cpp（架构文档技能）==")
    install_ash()
    print("完成。")
