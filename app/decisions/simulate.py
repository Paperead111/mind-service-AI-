"""预测模拟层（v4.1 G 公式）：多假设 + 预期自由能 + 反事实预演。

G = −[ 目标推进 + familiarity×主观兴趣 + κ·ΣΔPE ] + 风险惩罚 + λ(1−budget)·complexity

- 目标推进：目标栈 top3 优先级×动作增量 + 基础效用（冷启动对话推进项）
- κ·ΣΔPE（R15′）：认知闭合驱动，删除泛化信息增益，只留预测误差差减
- 风险惩罚：动作历史被拒率 + 负情绪
- λ(1−budget)·complexity（R11′）：代谢惩罚，budget 越低越省力
- familiarity（R14′）：非冲突类刺激痕迹 R，乘在主观兴趣上（兴趣 R2 接入前为 0）
- κ 优先级：振荡阻尼 0.1 > pending 补偿 0.4 > 默认 0.2（v4.1 裁定）
"""
from datetime import datetime, timezone

from app.db import Database, db
from app.logging_setup import get_logger, log_event

log = get_logger("decisions.simulate")

ACTION_DELTA = {  # 各动作对目标进度的期望增量（规则估计）
    "reply": 0.05, "counter_ask": 0.10, "lookup": 0.12, "silence": 0.0,
    "followup_view": 0.08, "contest": 0.15, "closing": 0.02, "skill": 0.06,
    "confront": 0.08,
}

# 冷启动基础效用（无目标时的对话推进项；按 λ(1−b)complexity 代谢项重新校准，
# 保证 budget=0.7 冷启动时 反问/查证/技能 仍能按预期胜出）
BASE_UTILITY = {
    "reply": 0.01, "counter_ask": 0.06, "lookup": 0.08, "silence": 0.0,
    "closing": 0.02, "contest": 0.04, "followup_view": 0.03, "skill": 0.10,
    "confront": 0.05,
}

# R15′：各动作对最高困惑关联的预测误差消减系数（κ·ΣΔPE 的 ΔPE 来源）
PE_REDUCE = {
    "reply": 0.05, "counter_ask": 0.30, "lookup": 0.50, "skill": 0.40,
    "silence": 0.0, "contest": 0.10, "closing": 0.05, "followup_view": 0.05,
    "confront": 0.10,
}

PE_GAIN_FACTOR = {   # G 计算里各动作"预期消解困惑"的系数
    "reply": 0.05, "counter_ask": 0.5, "lookup": 0.6, "skill": 0.8,
    "silence": 0.0, "contest": 0.2, "closing": 0.05, "followup_view": 0.1,
    "confront": 0.3,
}

PE_ACCUMULATE = 0.05   # 未知领域提问 → 最高困惑边 pred_error 累积量
RISK_WEIGHT = 1.0

KAPPA_DEFAULT = 0.2
KAPPA_PENDING = 0.4
KAPPA_DAMPING = 0.1


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def build_hypotheses(user_text: str, state: str) -> list[dict]:
    """规则版意图假设（2–5 个，带先验）。LLM 增强时替换本函数。"""
    hyps = [{"label": "陈述/分享", "prior": 0.4, "content": user_text[:30]}]
    if any(w in user_text for w in ("要不要", "我想", "打算", "试试")):
        hyps.append({"label": "寻求判断", "prior": 0.3, "content": user_text[:30]})
    if state in ("收束", "推开", "回避"):
        hyps.append({"label": "想要空间", "prior": 0.3, "content": user_text[:30]})
    if any(w in user_text for w in ("为什么", "怎么", "什么是", "什么", "吗", "呢")):
        hyps.append({"label": "提问求答", "prior": 0.3, "content": user_text[:30]})
    total = sum(h["prior"] for h in hyps) or 1.0
    for h in hyps:
        h["prior"] = round(h["prior"] / total, 3)
    return hyps


def est_goal_progress(action: str, database: Database | None = None) -> float:
    """目标推进 = 目标栈 top3 的 优先级 × 动作增量 + 基础效用。"""
    conn = (database or db).conn()
    rows = conn.execute(
        "SELECT priority FROM goals WHERE status='active' ORDER BY priority DESC LIMIT 3"
    ).fetchall()
    total = sum(r["priority"] * ACTION_DELTA.get(action, 0.05) for r in rows)
    return total + BASE_UTILITY.get(action, 0.01)


def pe3_of(state: dict | None) -> float:
    """快照里的 Σ(最高 3 边 pred_error)。"""
    if state and state.get("pe3") is not None:
        return float(state["pe3"])
    return 0.0


def est_pe_gain(action: str, state: dict | None) -> float:
    """κ·ΣΔPE 中的 ΣΔPE = 系数 × ΣPE3（预期消解困惑量）。"""
    return PE_GAIN_FACTOR.get(action, 0.05) * pe3_of(state)


def est_risk(action: str, database: Database | None = None) -> float:
    """风险 = 0.5×边界 + 0.3×历史被拒率 + 0.2×负情绪。"""
    conn = (database or db).conn()
    row = conn.execute(
        "SELECT accepted, rejected FROM action_outcomes WHERE action=?", (action,)
    ).fetchone()
    rejection = 0.0
    if row and (row["accepted"] + row["rejected"]) > 0:
        rejection = row["rejected"] / (row["accepted"] + row["rejected"])
    negative = 0.0
    try:
        e = conn.execute("SELECT valence FROM emotion_state WHERE id=1").fetchone()
        if e and e["valence"] < 0:
            negative = min(1.0, -e["valence"])
    except Exception:
        pass
    return 0.3 * rejection + 0.2 * negative


def kappa_for(database: Database | None = None, pending: bool = False,
              damping: bool = False) -> float:
    """κ 优先级：振荡阻尼 0.1 > pending 补偿 0.4 > 默认 0.2。"""
    if damping:
        return KAPPA_DAMPING
    if pending:
        return KAPPA_PENDING
    return KAPPA_DEFAULT


def expected_free_energy(action: str, hypotheses: list[dict],
                         database: Database | None = None,
                         state: dict | None = None,
                         familiarity: float = 1.0,
                         kappa: float = KAPPA_DEFAULT,
                         damping: bool = False) -> dict:
    """v4.1 全项 G + 各分量明细（完全详细日志用）。"""
    from app.life.homeostasis import metabolic_term
    b = float((state or {}).get("budget", 0.7))
    goal = est_goal_progress(action, database)
    subj = (state or {}).get("subjective") or {}
    interest = float(subj.get("interest", 0.0))
    fam_interest = familiarity * interest          # R2 接入前 = 0
    pe = kappa * est_pe_gain(action, state)
    risk = est_risk(action, database)
    metabolic = metabolic_term(action, b)
    g = -(goal + fam_interest + pe) + RISK_WEIGHT * risk + metabolic
    return {"goal": round(goal, 4), "interest": round(fam_interest, 4),
            "pe_term": round(pe, 4), "risk": round(risk, 4),
            "metabolic": round(metabolic, 4), "G": round(g, 4)}


def pick_action(candidates: list[str], hypotheses: list[dict],
                database: Database | None = None,
                state: dict | None = None,
                familiarity: float = 1.0,
                kappa: float = KAPPA_DEFAULT,
                damping: bool = False) -> tuple[str, float, dict]:
    """argmin G：返回 (动作, G, 各分量明细)。"""
    best, best_g, best_detail, all_detail = None, float("inf"), {}, []
    for a in candidates:
        d = expected_free_energy(a, hypotheses, database, state, familiarity,
                                 kappa, damping)
        all_detail.append({**d, "action": a})
        if d["G"] < best_g:
            best, best_g, best_detail = a, d["G"], d
    log_event("g_breakdown", candidates=all_detail, chosen=best,
              msg=f"G 全分解：{best} 胜出（G={round(best_g, 4)}）")
    return best, best_g, best_detail


# ---------- R15′ 认知闭合：pred_error 累积/消减/归一化 ----------

def accumulate_pred_error(database: Database | None = None) -> float:
    """未知领域提问 → 最高困惑边 pred_error += 0.05（钳 1.0）。"""
    dbx = database or db
    row = dbx.conn().execute(
        "SELECT id, pred_error FROM graph_edges ORDER BY pred_error DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return 0.0
    new = round(min(1.0, (row["pred_error"] or 0.0) + PE_ACCUMULATE), 4)
    dbx.conn().execute("UPDATE graph_edges SET pred_error=? WHERE id=?", (new, row["id"]))
    dbx.conn().commit()
    # R20′ 健康预警账本：归一化前的 24h 累积增量
    from app.life.maintenance import note_pe_accum
    note_pe_accum(row["id"], PE_ACCUMULATE, dbx)
    log_event("pe_accumulate", edge_id=row["id"], before=row["pred_error"],
              after=new, msg=f"pred_error 累积：最高困惑边 {row['pred_error']} → {new}")
    return new


def reduce_pred_error(action: str, database: Database | None = None) -> float:
    """回答成功 → 最高困惑边 pred_error ×(1−系数)（钳 ≥0）。"""
    dbx = database or db
    row = dbx.conn().execute(
        "SELECT id, pred_error FROM graph_edges ORDER BY pred_error DESC LIMIT 1"
    ).fetchone()
    if row is None or (row["pred_error"] or 0.0) <= 0:
        return 0.0
    factor = PE_REDUCE.get(action, 0.05)
    new = round(max(0.0, (row["pred_error"] or 0.0) * (1 - factor)), 4)
    dbx.conn().execute("UPDATE graph_edges SET pred_error=? WHERE id=?", (new, row["id"]))
    dbx.conn().commit()
    log_event("pe_reduce", action=action, factor=factor,
              before=row["pred_error"], after=new,
              msg=f"pred_error 消减：{action} ×{1 - factor} → {new}")
    return new


def normalize_pred_errors(database: Database | None = None) -> bool:
    """每 24h 按最大值归一化防饱和（仅作用于 G 计算；max=0 跳过）。"""
    dbx = database or db
    row = dbx.conn().execute(
        "SELECT MAX(pred_error) m FROM graph_edges").fetchone()
    mx = float(row["m"] or 0.0)
    if mx <= 0:
        return False
    dbx.conn().execute("UPDATE graph_edges SET pred_error=ROUND(pred_error/?, 4)", (mx,))
    dbx.conn().commit()
    log_event("pe_normalize", max_before=round(mx, 4),
              msg="pred_error 每日归一化（按最大值，仅作用于 G）")
    return True


def record_outcome(action: str, outcome: str, database: Database | None = None) -> None:
    """回合结束后回写 action_outcomes（学习闭环：下次 est_risk 随之变化）。"""
    conn = (database or db).conn()
    col = "accepted" if outcome == "accepted" else "rejected"
    conn.execute(
        f"INSERT INTO action_outcomes (action, {col}) VALUES (?,1)"
        " ON CONFLICT(action) DO UPDATE SET "
        f"{col}={col}+1",
        (action,),
    )
    conn.commit()
