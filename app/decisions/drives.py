"""动机层：五维驱动向量 + RPE（奖励预测误差）。

V(s) = Σ 驱动_i × 预期满足_i；RPE = R(s′) − V(s)
RPE > 0 → 强化该路径；RPE < −0.2 → 标记危险路径。
状态持久化在 drive_state 单行表。
"""
import json
from datetime import datetime, timezone

from app.db import Database, db

DIMS = ("curiosity", "competence", "coherence", "efficiency", "social_approval")
RPE_REINFORCE = 0.05
RPE_DANGER = -0.20


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class DriveSystem:
    def __init__(self, database: Database | None = None):
        self.db = database or db

    def state(self) -> dict:
        conn = self.db.conn()
        row = conn.execute("SELECT * FROM drive_state WHERE id=1").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO drive_state (id, updated_at) VALUES (1, ?)", (_now(),)
            )
            conn.commit()
            row = conn.execute("SELECT * FROM drive_state WHERE id=1").fetchone()
        state = {d: row[d] for d in DIMS}
        for k in ("reinforced", "suppressed", "danger_paths"):
            try:
                state[k] = json.loads(row[k])
            except (json.JSONDecodeError, TypeError):
                state[k] = []
        return state

    def nudge(self, deltas: dict) -> dict:
        """驱动微调：按增量调整驱动值（0~1 钳制），返回新状态。"""
        conn = self.db.conn()
        self.state()  # 确保单行存在
        for k, v in deltas.items():
            if k in DIMS:
                new = round(max(0.0, min(1.0, self.state()[k] + v)), 4)
                conn.execute(f"UPDATE drive_state SET {k}=? WHERE id=1", (new,))
        conn.execute("UPDATE drive_state SET updated_at=? WHERE id=1", (_now(),))
        conn.commit()
        return self.state()

    def expected_value(self, expected: dict) -> float:
        """V(s) = Σ 驱动_i × 预期满足_i。expected 只给部分维度也 OK。"""
        state = self.state()
        return sum(state[d] * expected.get(d, 0.0) for d in DIMS)

    def observe(self, actual: dict, action: str) -> float:
        """回合结束：R(s′) 计算 + RPE 更新 + 强化/危险路径记录。返回 rpe。"""
        reward = sum(self.state()[d] * actual.get(d, 0.0) for d in DIMS)
        expected = self.state().get("_last_expected", None)
        # 简化：RPE 以"实际满足 − 上次预期"计算，预期存于调用方传入
        rpe = reward - expected if expected is not None else 0.0
        self.update_rpe(rpe, action)
        return rpe

    def update_rpe(self, rpe: float, action: str) -> None:
        conn = self.db.conn()
        state = self.state()
        if rpe > RPE_REINFORCE:
            state["reinforced"] = state["reinforced"][-19:] + [action]
        if rpe < RPE_DANGER:
            state["danger_paths"] = state["danger_paths"][-19:] + [action]
        conn.execute(
            "UPDATE drive_state SET reinforced=?, danger_paths=?, updated_at=? WHERE id=1",
            (json.dumps(state["reinforced"], ensure_ascii=False),
             json.dumps(state["danger_paths"], ensure_ascii=False), _now()),
        )
        conn.commit()
