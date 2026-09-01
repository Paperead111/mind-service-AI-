"""生成参数包：主路径 LLM 的唯一数值输入（R10 起逐步充实，R11′-R24 各阶段补齐字段）。

v4.1 规格字段：energy / self_confidence / connection_reliability / familiarity /
target_sentence_length / target_paragraphs / punctuation_variety / pending_agenda /
highest_pe_edge / 主观兴趣 / 时钟数值 / discourse_act / discourse_trail / 技能与工具清单。

后端零静态输出：本包只含数值与结构化数据，不含任何面向用户的句子；
LLM 根据数值自行措辞。
"""


def build_generation_params(state: dict, decision: dict | None = None,
                            emotion: dict | None = None) -> dict:
    """把常驻状态快照 + 决策 + 情绪感知编译成数值参数包。"""
    b = max(0.0, min(1.0, float(state.get("budget", 0.7))))
    emo = emotion or {}
    dec = decision or {}
    return {
        # 内稳态/自我/连接（R11′/R13′/R18′ 逐步接管真实值）
        "energy": round(b, 3),
        "self_confidence": round(float(state.get("p_self", 0.85)), 3),
        "connection_reliability": round(float(state.get("connection_reliability", 1.0)), 3),
        "familiarity": round(float(state.get("familiarity", 1.0)), 3),
        # 疲劳光谱（R11′：随 budget 连续退行）
        "target_sentence_length": round(18 + 12 * b, 1),
        "target_paragraphs": 1 + int(2 * b),
        "punctuation_variety": round(0.3 + 0.5 * b, 2),
        # 议程与认知（R12′/R15′ 接入）
        "pending_agenda": list(state.get("pending_agenda") or []),
        "highest_pe_edge": state.get("top_pe_edge"),
        # 主观与时钟（R2/R6 接入）
        "subjective_interest": dict(state.get("subjective") or {}),
        "clock": {"loneliness": round(float(state.get("loneliness", 0.0)), 3),
                  "silent_ticks": int(state.get("silent_ticks", 0))},
        # 话语流（R24 接入）
        "discourse_act": dec.get("discourse_act"),
        "discourse_trail": list(state.get("discourse_trail") or []),
        # 情绪感知
        "emotion": {
            "valence": round(float(emo.get("valence", 0.0)), 3),
            "arousal": round(float(emo.get("arousal", 0.4)), 3),
            "dominant": emo.get("emotion_cn") or state.get("dominant") or "平静",
            "intensity": round(float(emo.get("intensity") or 0.0), 1),
        },
        # 行动（决策系统输出）
        "action": dec.get("action"),
    }


def params_to_prompt_block(params: dict) -> str:
    """参数包 → 提示词块（结构标签 + 数值；不含任何固定例句）。"""
    import json
    body = json.dumps(params, ensure_ascii=False, default=str)
    return (
        "[状态参数]\n"
        f"{body}\n"
        "[参数使用] 依据数值调整措辞：energy 越低，句子必须越短、总回复越短、多留白；"
        "target_sentence_length 是每句字数的硬上限，严格遵守；"
        "self_confidence 低→少用确定口吻；其余字段按语义取用，不确定就用中间值。"
    )
