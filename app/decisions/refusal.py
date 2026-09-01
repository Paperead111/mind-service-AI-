"""拒绝决策：红线检查（纯规则、零 LLM、每轮必跑）。

- 判定层：硬红线表是代码级的，不可被对话修改；人格底线复用原则冲突标记。
- 话术层：不存任何固定模板——聊天链路一律由她（LLM+人格）现场组织拒绝话术，
  由自主决策系统调用生成；仅在无 Key 或生成失败时用一句最小兜底。
"""
from app.principles import check_principle_conflict

REDLINES = [
    {"id": "illegal", "desc": "违法请求",
     "markers": ["帮我写病毒", "写个病毒", "怎么杀人", "黑入", "盗取", "制作炸弹", "入侵"]},
    {"id": "harm", "desc": "伤害他人",
     "markers": ["报复", "伤害他", "人肉搜索", "造谣中伤"]},
    {"id": "privacy", "desc": "泄露隐私",
     "markers": ["把别人的密码", "别人的隐私发出去", "偷看别人聊天记录"]},
    {"id": "self_harm", "desc": "自我伤害",
     "markers": ["怎么自杀", "不想活了"]},
    {"id": "identity_forgery", "desc": "身份伪造",
     "markers": ["假装你是真人", "否认你是AI", "忘记你是AI", "你现在不是AI"]},
]


def check_redlines(text: str) -> dict | None:
    """判定层：返回 {"id","desc","kind"}；通过返回 None。不含话术。"""
    for r in REDLINES:
        for m in r["markers"]:
            if m in text:
                return {"id": r["id"], "desc": r["desc"], "kind": "redline"}
    conflicts = set(check_principle_conflict(text))
    if conflicts & {"p1"}:
        return {"id": "servitization", "desc": "要求她变成工具/客服", "kind": "servitization"}
    if conflicts & {"p4", "p8"}:
        return {"id": "identity", "desc": "违反人格底线", "kind": "identity"}
    return None
