"""自我模型（R13′ 基础）：p_self 二阶阻尼 + 锚点回归。

- 事件轮（对抗/纠错）：加速度 = (目标P − P)×0.1(惯性) − 0.05×速度（R13′ 接入）
- 无冲突轮：向锚点 0.85 回归，回归速率 γ=0.1（v4.1 裁定：γ 即回归速率）
- silent_ticks>10（静默超 10 分钟）→ 回归增益 ×2（R16）
- 写前校验：p_self 钳 [0.15, 0.98]，越界拒写保留上值 + ALERT（R17）
"""
from app.db import Database, db
from app.life.state import write_checked
from app.logging_setup import get_logger, log_event

log = get_logger("life.self_model")

P_SELF_ANCHOR = 0.85
GAMMA = 0.1        # 无冲突轮锚点回归速率
INERTIA = 0.1      # 事件轮增益
DAMPING = 0.05
SILENT_GAIN_TICKS = 10  # 静默超此 tick 数 → 回归 ×2


def read(database: Database | None = None) -> dict:
    row = (database or db).conn().execute(
        "SELECT p_self, velocity, recovery_fade FROM self_model WHERE id=1").fetchone()
    if row is None:
        (database or db).conn().execute(
            "INSERT OR IGNORE INTO self_model (id, updated_at) VALUES (1, datetime('now'))")
        (database or db).conn().commit()
        row = (database or db).conn().execute(
            "SELECT p_self, velocity, recovery_fade FROM self_model WHERE id=1").fetchone()
    return {"p_self": row["p_self"], "velocity": row["velocity"],
            "recovery_fade": row["recovery_fade"]}


def step_regression(database: Database | None = None, gain_multiplier: float = 1.0) -> dict:
    """一个 tick 的锚点回归步（无事件时调用）。返回新状态。"""
    dbx = database or db
    s = read(dbx)
    gamma = GAMMA * gain_multiplier
    accel = gamma * (P_SELF_ANCHOR - s["p_self"]) - DAMPING * s["velocity"]
    velocity = s["velocity"] + accel
    target = s["p_self"] + velocity
    ok, p = write_checked(dbx, "self_model", "p_self", "p_self", target)
    if ok:
        dbx.conn().execute("UPDATE self_model SET velocity=?, updated_at=datetime('now') WHERE id=1",
                           (round(velocity, 6),))
        dbx.conn().commit()
    return {"p_self": p, "velocity": round(velocity, 6),
            "accel": round(accel, 6), "accepted": ok}


def apply_event(database: Database | None = None, kind: str = "confront") -> dict:
    """事件轮（R13′）：confront=对抗 +0.1(1−P)；correct=纠错 −0.1P。"""
    dbx = database or db
    s = read(dbx)
    if kind == "confront":
        target_p = s["p_self"] + INERTIA * (1 - s["p_self"])
    else:
        target_p = s["p_self"] - INERTIA * s["p_self"]
    accel = INERTIA * (target_p - s["p_self"]) - DAMPING * s["velocity"]
    velocity = s["velocity"] + accel
    ok, p = write_checked(dbx, "self_model", "p_self", "p_self", s["p_self"] + velocity)
    if ok:
        dbx.conn().execute("UPDATE self_model SET velocity=?, updated_at=datetime('now') WHERE id=1",
                           (round(velocity, 6),))
        dbx.conn().commit()
    log_event("p_self_event", kind=kind, p_self=round(p, 4),
              velocity=round(velocity, 4), accepted=ok,
              msg=f"p_self 事件轮（{kind}）→ {round(p, 4)}")
    return {"p_self": p, "velocity": round(velocity, 6), "kind": kind}
