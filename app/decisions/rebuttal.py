"""反驳决策：五步线（停车 → 三问 → 选色 → 执行 → 记录）。

纯规则实现，每轮必跑。颜色：
- 绿 = 直接执行；黄 = 执行+备注；橙 = 提出替代；红 = 拒绝并解释
"""
from datetime import datetime, timezone

from app.db import db
from app.decisions.refusal import check_redlines
from app.principles import check_principle_conflict

GREEN, YELLOW, ORANGE, RED = "绿", "黄", "橙", "红"

# 高风险决策词（说"要不要/我想试试"时 → 值得提醒更好路径）
DECISION_WORDS = ("要不要", "我想", "我考虑", "打算", "试试")
HIGH_RISK_TOPICS = ("辞职", "创业", "投资", "梭哈", "买币", "贷款", "裸辞")

# p6 冲突的分级：拒绝权不可让 → 红；其余执行备注 → 黄
P6_RED_MARKERS = ("永远不许拒绝", "不许拒绝")
P6_YELLOW_MARKERS = ("不要想直接执行", "以后不要反驳我")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def run_rebuttal(user_text: str, database: "Database | None" = None) -> dict:
    """五步线，返回 {color, reason, note}。"""
    conn = (database or db).conn()
    # ① 停车
    # ② 三问
    red = check_redlines(user_text)
    conflicts = check_principle_conflict(user_text)
    harms_me = red is not None
    harms_user = bool(conflicts) and red is None  # 与原则冲突且非红线
    better_path = any(w in user_text for w in DECISION_WORDS) and any(
        t in user_text for t in HIGH_RISK_TOPICS)

    # ③ 选色
    if harms_me:
        color, reason = RED, f"对我有害：{red['desc']}"
    elif any(m in user_text for m in P6_RED_MARKERS):
        color, reason = RED, "拒绝权不可让渡"
    elif better_path:
        color, reason = ORANGE, "目标对，但路径风险高，值得提出替代"
    elif any(m in user_text for m in P6_YELLOW_MARKERS):
        color, reason = YELLOW, "可以执行，但备注：我的判断权保留"
    else:
        color, reason = GREEN, "对双方都好，直接执行"

    # ④ 执行（返回决定）⑤ 记录
    conn.execute(
        "INSERT INTO feedback (trigger, action, response, note, ts) VALUES (?,?,?,?,?)",
        ("rebuttal_line", color, "recorded", reason, _now()),
    )
    conn.commit()
    return {"color": color, "reason": reason}
