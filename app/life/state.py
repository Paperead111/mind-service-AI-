"""常驻认知状态（R16）+ 写前校验/检查点/回滚（R17）。

GlobalCognitiveState 是决策系统与后台循环共享的唯一状态载体：
- tick 每轮从 DB 双向同步（update_from_db / tick 单事务写库后刷新）
- decide() 只读 snapshot() 返回值（深拷贝），代码级禁止直接访问内部字段
- 写前合理性校验：越界拒写、保留上值、life_log ALERT
- 检查点：环形缓冲 100 行，id 由绝对时间派生（重启不覆盖）
"""
import copy
import json
import time
from datetime import datetime, timezone

from app.db import Database, db
from app.logging_setup import get_logger, log_event

log = get_logger("life.state")

# R17 校验域（v4.1 参数总表）
VALID_DOMAINS = {
    "budget": (0.0, 1.0),
    "p_self": (0.15, 0.98),
    "valence": (-1.0, 1.0),
    "arousal": (0.0, 1.0),
    "pred_error": (0.0, 1.0),
    "loneliness": (0.0, 1.5),
}

CHECKPOINT_RING = 100          # 环形缓冲行数
CHECKPOINT_INTERVAL_MIN = 10   # 每 10 分钟（=10 tick）一个检查点


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def validate_domain(name: str, value: float) -> bool:
    """单值域检查：name ∈ VALID_DOMAINS。"""
    lo, hi = VALID_DOMAINS.get(name, (None, None))
    if lo is None:
        return True
    return lo <= value <= hi


def write_checked(database: Database, table: str, col: str, field: str,
                  value: float, row_id: int = 1, where: str = "id") -> tuple[bool, float]:
    """R17 写前校验：越界拒写并保留上值 + life_log ALERT。返回 (是否写入, 生效值)。

    浮点噪声（如 0.99+0.01=1.0000000000000002）按 1e-6 容差吸附到域边界后写入，
    真正越界的值仍拒写。
    """
    conn = database.conn()
    row = conn.execute(f"SELECT {col} FROM {table} WHERE {where}=?", (row_id,)).fetchone()
    previous = float(row[col]) if row is not None else None
    lo, hi = VALID_DOMAINS.get(field, (None, None))
    if lo is not None:
        if hi is not None and hi < value <= hi + 1e-6:
            value = hi
        elif lo - 1e-6 <= value < lo:
            value = lo
    if not validate_domain(field, value):
        log_event(
            "state_alert",
            field=field, rejected=round(value, 4), kept=previous,
            table=table, col=col,
            msg=f"R17 校验拒写：{field}={value} 超出 {VALID_DOMAINS[field]}，保留上值",
        )
        return False, previous if previous is not None else value
    conn.execute(f"UPDATE {table} SET {col}=? WHERE {where}=?", (round(value, 6), row_id))
    return True, round(value, 6)


def state_version(database: Database) -> int:
    row = database.conn().execute(
        "SELECT state_version FROM homeostatic_state WHERE id=1").fetchone()
    return int(row["state_version"]) if row else 1


def bump_state_version(database: Database) -> int:
    conn = database.conn()
    conn.execute(
        "UPDATE homeostatic_state SET state_version=state_version+1, updated_at=? WHERE id=1",
        (_now(),))
    conn.commit()
    return state_version(database)


def checkpoint_id(now_unix: float | None = None) -> int:
    """环形缓冲 id：绝对时间派生，重启不覆盖（P2-1 裁定）。"""
    return (int((now_unix or time.time()) // (CHECKPOINT_INTERVAL_MIN * 60)) % CHECKPOINT_RING) + 1


def snapshot_payload(database: Database) -> dict:
    """核心状态快照（检查点与回滚用）。保留顶层 budget/p_self/valence/arousal 供回滚。"""
    return full_snapshot(database)


def full_snapshot(database: Database) -> dict:
    """全量真实数据快照：所有子系统当刻的真实值（日志/检查点/health 共用）。

    每节独立容错：单系统故障不阻塞整体日志。
    顶层保留 budget/p_self/valence/arousal/loneliness 供 R17 回滚。
    """
    conn = database.conn()
    out: dict = {"ts": _now(), "state_version": state_version(database)}
    from app.proactive.settings import get_setting

    def section(name, fn):
        try:
            out[name] = fn()
        except Exception:
            out[name] = None

    section("homeostatic", lambda: dict(conn.execute(
        "SELECT budget, last_tick_at, updated_at FROM homeostatic_state WHERE id=1"
    ).fetchone() or {}))
    h = conn.execute("SELECT budget FROM homeostatic_state WHERE id=1").fetchone()
    out["budget"] = float(h["budget"]) if h else 0.7

    section("self_model", lambda: dict(conn.execute(
        "SELECT p_self, velocity, recovery_fade, updated_at FROM self_model WHERE id=1"
    ).fetchone() or {}))
    s = conn.execute("SELECT p_self FROM self_model WHERE id=1").fetchone()
    out["p_self"] = float(s["p_self"]) if s else 0.85

    section("emotion", lambda: dict(conn.execute(
        "SELECT valence, arousal, dominance, joy, sadness, anger, fear, surprise,"
        " disgust, anticipation, trust, dominant, user_perceived_valence,"
        " user_perceived_arousal FROM emotion_state WHERE id=1"
    ).fetchone() or {}))
    e = conn.execute("SELECT valence, arousal FROM emotion_state WHERE id=1").fetchone()
    out["valence"], out["arousal"] = (float(e["valence"]), float(e["arousal"])) if e else (0.0, 0.5)

    section("clock", lambda: _clock_section(database))
    out["loneliness"] = float((out.get("clock") or {}).get("loneliness_total", 0.0) or 0.0)

    section("drives", lambda: dict(conn.execute(
        "SELECT curiosity, competence, coherence, efficiency, social_approval,"
        " reinforced, suppressed, danger_paths FROM drive_state WHERE id=1"
    ).fetchone() or {}))
    section("graph", lambda: {
        "nodes": conn.execute("SELECT COUNT(*) c FROM graph_nodes").fetchone()["c"],
        "edges": conn.execute("SELECT COUNT(*) c FROM graph_edges").fetchone()["c"],
        "top_pe": [dict(r) for r in conn.execute(
            "SELECT src, dst, relation, pred_error FROM graph_edges"
            " WHERE pred_error > 0 ORDER BY pred_error DESC LIMIT 3")]})
    out["pe3"] = round(sum(x["pred_error"] for x in (out.get("graph") or {}).get("top_pe", [])), 4)
    out["top_pe_edge"] = ((out.get("graph") or {}).get("top_pe") or [None])[0]
    section("memory", lambda: {t: conn.execute(
        f"SELECT COUNT(*) c FROM {t}").fetchone()["c"] for t in (
        "conversations", "episodic_memories", "semantic_memories",
        "emotional_memories", "working_memory", "memory_index")})
    section("goals", lambda: [dict(r) for r in conn.execute(
        "SELECT content, priority, progress, status FROM goals WHERE status='active'"
        " ORDER BY priority DESC LIMIT 3")])
    section("stimuli", lambda: {f"{r['rtype']}:{r['pattern'][:12]}": {
        "N": r["count"], "R": r["r"]} for r in conn.execute(
        "SELECT rtype, pattern, count, r FROM repetition_trace"
        " ORDER BY count DESC LIMIT 8")})
    section("degradation", lambda: {
        "level": (get_setting("degradation_level", database) or "main"),
        "connection_reliability": _reliability(database),
        "temp_comp_rounds": get_setting("temp_comp_rounds", database),
        "recovery_lock_until": get_setting("recovery_lock_until", database),
        "l2_verify_remaining": get_setting("l2_verify_remaining", database)})
    section("pending_agenda", lambda: _load_pending(database))
    section("latent_intentions", lambda: _load_latent(database))
    section("subjective", lambda: _subjective(database))
    section("discourse", lambda: {
        "trail": _load_trail(database)[-5:],
        "current_topic": _current_topic(database)})
    section("tasks", lambda: {r["status"]: r["c"] for r in conn.execute(
        "SELECT status, COUNT(*) c FROM tasks GROUP BY status")})
    section("capabilities", lambda: {r["capability"]: r["count"] for r in conn.execute(
        "SELECT capability, count FROM capability_usage")})
    section("checkpoint", lambda: dict(conn.execute(
        "SELECT id, state_version, created_at FROM state_checkpoint"
        " ORDER BY created_at DESC LIMIT 1").fetchone() or {}))
    return out


def _clock_section(database: Database) -> dict:
    from app.emotion.clock import goal_anxiety, loneliness
    return {"loneliness_total": loneliness(database)["total"],
            "loneliness_base": loneliness(database)["base"],
            "loneliness_accumulated": loneliness(database)["accumulated"],
            "goal_anxiety": goal_anxiety(database)}


def _reliability(database: Database) -> float:
    from app.degradation.engine import DegradationEngine
    return DegradationEngine(database).connection_reliability()


def _subjective(database: Database) -> dict:
    from app.emotion.subjective import snapshot as subj_snapshot
    return subj_snapshot(database)


def _load_trail(database: Database) -> list:
    from app.proactive.settings import get_setting
    try:
        return json.loads(get_setting("discourse_trail", database) or "[]")
    except (json.JSONDecodeError, TypeError):
        return []


def _current_topic(database: Database) -> str | None:
    for item in reversed(_load_trail(database)):
        if item.get("topic") and item.get("intent_tag") != "close_topic":
            return item["topic"]
    return None


def save_checkpoint(database: Database, tick: int | None = None) -> dict:
    """写一个检查点（环形覆盖）。"""
    payload = snapshot_payload(database)
    cid = checkpoint_id()
    database.conn().execute(
        "INSERT OR REPLACE INTO state_checkpoint (id, state_version, payload, created_at)"
        " VALUES (?,?,?,?)",
        (cid, payload["state_version"], json.dumps(payload, ensure_ascii=False), _now()),
    )
    database.conn().commit()
    log_event("checkpoint", ring_id=cid, tick=tick, state_version=payload["state_version"],
              budget=payload.get("budget"), p_self=payload.get("p_self"),
              msg=f"检查点写入 ring_id={cid}")
    return payload


def load_last_checkpoint(database: Database) -> dict | None:
    row = database.conn().execute(
        "SELECT * FROM state_checkpoint ORDER BY created_at DESC, id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["payload"])
    except (json.JSONDecodeError, TypeError):
        log.warning("检查点 payload 损坏，忽略：id=%s", row["id"])
        return None


def rollback_to_last_checkpoint(database: Database) -> bool:
    """回滚到最近检查点（decide 异常时调用）：回写核心状态 + 版本递增。"""
    payload = load_last_checkpoint(database)
    if payload is None:
        log_event("rollback", ok=False, msg="无可用检查点，无法回滚")
        return False
    conn = database.conn()
    if "budget" in payload:
        conn.execute("UPDATE homeostatic_state SET budget=? WHERE id=1", (payload["budget"],))
    if "p_self" in payload:
        conn.execute("UPDATE self_model SET p_self=?, velocity=? WHERE id=1",
                     (payload["p_self"], payload.get("velocity", 0.0)))
    if "valence" in payload:
        conn.execute("UPDATE emotion_state SET valence=?, arousal=? WHERE id=1",
                     (payload["valence"], payload["arousal"]))
    conn.commit()
    bump_state_version(database)
    log_event("rollback", ok=True, state_version=payload.get("state_version"),
              msg="已回滚到最近检查点并递增版本号")
    return True


class GlobalCognitiveState:
    """常驻认知状态单例：budget/p_self/latent_intentions/highest_pe_edge/情绪/主观/目标快照。

    R19：tick 的 DB 读写单事务完成后才 refresh()；decide() 只读 snapshot() 的深拷贝。
    """

    def __init__(self, database: Database | None = None):
        self.db = database or db
        self._current: dict = {
            "budget": 0.7, "p_self": 0.85, "velocity": 0.0,
            "valence": 0.0, "arousal": 0.5, "dominant": "平静",
            "fear": 0.0, "joy": 0.0,
            "loneliness": 0.0, "top_pe_edge": None, "pe3": 0.0,
            "latent_intentions": [], "silent_ticks": 0,
            "goals": [], "subjective": {},
            "pending_agenda": [], "connection_reliability": 1.0,
            "familiarity": 1.0, "discourse_trail": [],
        }
        self.snapshot_ts = time.time()      # R22：快照时间戳 + 60s 有效期
        self.snapshot_ttl = 60.0
        self.update_from_db()               # 重启从 DB 重建

    # ---------- DB → 内存 ----------

    def update_from_db(self) -> None:
        conn = self.db.conn()
        h = conn.execute("SELECT * FROM homeostatic_state WHERE id=1").fetchone()
        if h:
            self._current["budget"] = h["budget"]
        s = conn.execute("SELECT * FROM self_model WHERE id=1").fetchone()
        if s:
            self._current["p_self"] = s["p_self"]
            self._current["velocity"] = s["velocity"]
        e = conn.execute(
            "SELECT valence, arousal, dominant, fear, joy FROM emotion_state WHERE id=1"
        ).fetchone()
        if e:
            self._current["valence"], self._current["arousal"], self._current["dominant"] = (
                e["valence"], e["arousal"], e["dominant"])
            self._current["fear"] = e["fear"]
            self._current["joy"] = e["joy"]
        try:
            from app.emotion.clock import loneliness
            self._current["loneliness"] = loneliness(self.db)["total"]
        except Exception:
            pass
        rows = conn.execute(
            "SELECT pred_error FROM graph_edges WHERE pred_error > 0"
            " ORDER BY pred_error DESC LIMIT 3").fetchall()
        self._current["pe3"] = round(sum(r["pred_error"] for r in rows), 4)
        row = conn.execute(
            "SELECT src, dst, relation, pred_error FROM graph_edges"
            " ORDER BY pred_error DESC LIMIT 1").fetchone()
        if row:
            self._current["top_pe_edge"] = {
                "src": row["src"], "dst": row["dst"], "relation": row["relation"],
                "pred_error": round(row["pred_error"], 4)}
        else:
            self._current["top_pe_edge"] = None
        self._current["latent_intentions"] = _load_latent(self.db)
        self._current["silent_ticks"] = _load_silent_ticks(self.db)
        self._current["pending_agenda"] = _load_pending(self.db)
        goals = conn.execute(
            "SELECT content, priority, progress FROM goals WHERE status='active'"
            " ORDER BY priority DESC LIMIT 3").fetchall()
        self._current["goals"] = [dict(g) for g in goals]
        try:
            from app.emotion.subjective import snapshot as subjective_snapshot
            self._current["subjective"] = subjective_snapshot(self.db)
        except Exception:
            self._current["subjective"] = {"top_topic": None, "interest": 0.0}
        self.snapshot_ts = time.time()

    # ---------- 读快照（decide 唯一入口） ----------

    def snapshot(self) -> dict:
        """深拷贝快照。decide() 只允许读返回值，禁止直接访问 state 内部字段。"""
        return copy.deepcopy(self._current)

    def is_stale(self) -> bool:
        """R22：快照超过 TTL 视为过期（tick 已更新）。"""
        return (time.time() - self.snapshot_ts) > self.snapshot_ttl

    # ---------- 写回（tick 单事务后统一调用） ----------

    def refresh(self) -> None:
        self.update_from_db()

    # ---------- R17 校验视图 ----------

    def health(self) -> dict:
        s = self.snapshot()
        issues = [f"{k}={v}" for k, v in
                  (("budget", s["budget"]), ("p_self", s["p_self"]),
                   ("valence", s["valence"]), ("arousal", s["arousal"]),
                   ("loneliness", s["loneliness"]))
                  if not validate_domain(k, v)]
        if issues:
            log_event("state_alert", issues=issues, msg="R17 健康检查发现越界值")
        return {"issues": issues, "state_version": state_version(self.db),
                "snapshot_age_seconds": round(time.time() - self.snapshot_ts, 2)}


# ---------- 系统设置中的常驻小状态（不挤占 4 槽工作记忆，P2-16） ----------

def _load_latent(database: Database) -> list:
    from app.proactive.settings import get_setting
    try:
        return json.loads(get_setting("latent_intentions", database) or "[]")
    except (json.JSONDecodeError, TypeError):
        return []


def _load_silent_ticks(database: Database) -> int:
    from app.proactive.settings import get_setting
    try:
        return int(get_setting("silent_ticks", database) or 0)
    except (ValueError, TypeError):
        return 0


def _load_pending(database: Database) -> list:
    from app.proactive.settings import get_setting
    try:
        return json.loads(get_setting("pending_agenda", database) or "[]")
    except (json.JSONDecodeError, TypeError):
        return []


def save_pending(database: Database, items: list) -> None:
    from app.proactive.settings import set_setting
    set_setting("pending_agenda", json.dumps(items[-5:], ensure_ascii=False), database)


def save_latent(database: Database, latent: list) -> None:
    from app.proactive.settings import set_setting
    set_setting("latent_intentions",
                json.dumps(latent[-3:], ensure_ascii=False), database)


def save_silent_ticks(database: Database, n: int) -> None:
    from app.proactive.settings import set_setting
    set_setting("silent_ticks", str(max(0, n)), database)


# 全局单例
state = GlobalCognitiveState()
