"""R16 静默决策模拟：每 5 tick 一次，零 LLM。

高误差 3 边 + 未完结话题 → 2~3 个假设行动 → G_试算 = −认知效用 + 代谢惩罚（简化式，
v4.1 裁定：不回填主 G 其余项）→ argmin → latent_intention 持久化（独立槽位，
不挤占 4 槽工作记忆）→ 满足孵化阈值时写"预备信号"（life_log + 前端未读徽标，零文本）。
"""
import time

from app.config import settings
from app.db import Database, db
from app.life.state import (GlobalCognitiveState, save_latent,
                            save_silent_ticks)
from app.logging_setup import get_logger, log_event

log = get_logger("life.planning")

# 静默候选行动：(认知增益系数 × ΣPE3, 代谢惩罚)
SILENT_ACTIONS = [
    {"name": "observe_edge", "cognitive_gain": 0.30, "metabolic": 0.010},
    {"name": "pending_question", "cognitive_gain": 0.50, "metabolic": 0.030},
    {"name": "self_note", "cognitive_gain": 0.15, "metabolic": 0.005},
]

INCUBATE_G = -0.05      # 孵化阈值（与 R7 共用同一入口）
INCUBATE_BUDGET = 0.4


def top_pe_edges(database: Database | None = None, n: int = 3) -> list[dict]:
    conn = (database or db).conn()
    rows = conn.execute(
        "SELECT src, dst, relation, pred_error FROM graph_edges"
        " WHERE pred_error > 0 ORDER BY pred_error DESC LIMIT ?", (n,)).fetchall()
    return [dict(r) for r in rows]


def unfinished_topics(database: Database | None = None) -> list[str]:
    """未完结话题：工作记忆 + 未完成目标（零 LLM 读取）。"""
    conn = (database or db).conn()
    topics: list[str] = []
    for r in conn.execute(
            "SELECT content FROM working_memory ORDER BY last_access DESC LIMIT 2"):
        topics.append(r["content"][:30])
    for r in conn.execute(
            "SELECT content FROM goals WHERE status='active' AND progress < 1"
            " ORDER BY priority DESC LIMIT 1"):
        topics.append("目标:" + r["content"][:30])
    return topics


def simulate_silent_planning(database: Database | None = None,
                             state: GlobalCognitiveState | None = None,
                             tick: int | None = None) -> dict:
    """一次静默决策模拟。返回结构化结果（零文本输出）。"""
    dbx = database or db
    snap = (state or GlobalCognitiveState(dbx)).snapshot()
    edges = top_pe_edges(dbx)
    pe3 = sum(e["pred_error"] for e in edges)
    topics = unfinished_topics(dbx)

    candidates = [
        {"name": "observe_edge", "cognitive_gain": 0.30, "metabolic": 0.010},
        {"name": "self_note", "cognitive_gain": 0.15, "metabolic": 0.005},
    ]
    if topics:
        candidates.append({"name": "pending_question",
                           "cognitive_gain": 0.50, "metabolic": 0.030})

    best, best_g = None, float("inf")
    detail = {}
    for a in candidates:
        cognitive = a["cognitive_gain"] * pe3
        g = -cognitive + a["metabolic"]       # 简化式：−认知效用 + 代谢惩罚
        if g < best_g:
            best, best_g, detail = a, g, {
                "cognitive": round(cognitive, 4),
                "metabolic": a["metabolic"],
                "pe3": round(pe3, 4),
            }

    top_edge = edges[0] if edges else None
    latent = {
        "tick": tick or 0,
        "ts": int(time.time()),
        "action": best["name"],
        "G": round(best_g, 4),
        "cognitive": detail.get("cognitive", 0.0),
        "metabolic": detail.get("metabolic", 0.0),
        "edge": (f"{top_edge['src']}-{top_edge['relation']}->{top_edge['dst']}"
                 if top_edge else None),
        "topic": topics[0] if topics else None,
    }
    current = list(snap.get("latent_intentions") or [])
    current.append(latent)
    save_latent(dbx, current)

    prep = best_g < INCUBATE_G and snap["budget"] > INCUBATE_BUDGET
    if prep:
        from app.proactive.settings import set_setting
        set_setting("unread_notify", str(int(time.time())), dbx)
        log_event("prep_signal", tick=tick or 0, G=round(best_g, 4),
                  budget=round(snap["budget"], 3), edge=latent["edge"],
                  msg="预备信号：静默孵化命中阈值，写未读徽标（零文本）")

    log_event(
        "silent_planning",
        tick=tick or 0,
        pe3=detail.get("pe3", 0.0),
        candidates=len(candidates),
        action=best["name"],
        G=round(best_g, 4),
        prep_signal=prep,
        latent_count=len(current),
        budget=round(snap.get("budget", 0.7), 4),
        p_self=round(snap.get("p_self", 0.85), 4),
        latent=current[-1] if current else None,
        msg=f"静默规划：选 {best['name']}（G={round(best_g, 4)}，ΣPE3={detail.get('pe3', 0)})",
    )
    return latent
