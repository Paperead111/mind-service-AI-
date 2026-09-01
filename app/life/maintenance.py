"""健康自检与长期运维（R20′）。

- /v1/health/deep 六指标：budget 1h 波动 / p_self 速度 / pred_error 24h 累积增量 /
  最后成功 LLM / 当前降级级 / 状态版本+最近检查点
- 振荡检测：近 10 轮 budget 标准差>0.15 且 SILENCE/LOOKUP 交替>6 → κ 强制 0.1×30 分钟
- 每日维护（tick 钩子）：pred_error 归一化 / repetition_trace 7 天衰减 /
  life_log 7 天明细+日摘要归档 / 周报摘要
"""
import json
import time
from datetime import datetime, timedelta, timezone

from app.db import Database, db
from app.logging_setup import get_logger, log_event
from app.proactive.settings import get_setting, set_setting

log = get_logger("life.maintenance")

LIFE_LOG_KEEP_DAYS = 7
TRACE_DECAY_DAYS = 7
OSCILLATION_STD = 0.15
OSCILLATION_ALTERNATE = 6
DAMPING_SECONDS = 30 * 60
PE_ACCUM_WARN = 1.0      # pred_error 24h 累积增量预警阈值（可配置）


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _today() -> str:
    return _now().strftime("%Y-%m-%d")


# ---------- 健康自检 ----------

def health_deep(database: Database | None = None) -> dict:
    dbx = database or db
    conn = dbx.conn()

    # ① budget 近 1h 波动（life_log 明细里带 budget 字段）
    rows = conn.execute(
        "SELECT detail FROM life_log WHERE event='tick' ORDER BY id DESC LIMIT 60"
    ).fetchall()
    budgets = []
    for r in reversed(rows):
        try:
            d = json.loads(r["detail"] or "{}")
            if "budget" in d:
                budgets.append(float(d["budget"]))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    if len(budgets) >= 2:
        mean = sum(budgets) / len(budgets)
        var = sum((b - mean) ** 2 for b in budgets) / len(budgets)
        budget_std = round(var ** 0.5, 4)
    else:
        budget_std = 0.0

    # ② p_self 速度
    sm = conn.execute("SELECT p_self, velocity FROM self_model WHERE id=1").fetchone()
    velocity = float(sm["velocity"]) if sm else 0.0

    # ③ pred_error 预警：只看归一化前的 24h 累积增量（P0-2 裁定）
    pe_warning = pe_24h_accum(database) >= PE_ACCUM_WARN
    pe_accum_24h = round(pe_24h_accum(database), 4)

    # ④ 最后成功 LLM 时间
    last_llm = get_setting("last_llm_success_at", dbx)

    # ⑤ 当前降级级
    from app.degradation.engine import DegradationEngine
    level = DegradationEngine(dbx).get_level()

    # ⑥ 状态版本 + 最近检查点
    cp = conn.execute(
        "SELECT id, state_version, created_at FROM state_checkpoint"
        " ORDER BY created_at DESC LIMIT 1").fetchone()
    version = conn.execute(
        "SELECT state_version FROM homeostatic_state WHERE id=1").fetchone()
    state_version = version["state_version"] if version else 1

    # 振荡检测（触发 → κ=0.1×30min）
    oscillating = detect_oscillation(database)

    return {
        "budget_std_1h": budget_std,
        "p_self_velocity": round(velocity, 5),
        "p_self_velocity_ok": abs(velocity) < 0.1,
        "pred_error_accum_24h": pe_accum_24h,
        "pred_error_warning": pe_warning,
        "last_llm_success_at": last_llm,
        "degradation_level": level,
        "state_version": state_version,
        "last_checkpoint": {"id": cp["id"], "version": cp["state_version"],
                            "created_at": cp["created_at"]} if cp else None,
        "oscillation_damping": oscillating,
    }


def detect_oscillation(database: Database | None = None) -> bool:
    """近 10 轮 budget 标准差>0.15 且 SILENCE/LOOKUP 交替>6 → 阻尼 30 分钟。"""
    dbx = database or db
    conn = dbx.conn()
    rows = conn.execute(
        "SELECT action, budget FROM decision_log WHERE action IN ('SILENCE','LOOKUP')"
        " ORDER BY id DESC LIMIT 10").fetchall()
    if len(rows) < 8:
        return False
    actions = [r["action"] for r in reversed(rows)]
    alternations = sum(1 for i in range(1, len(actions))
                       if actions[i] != actions[i - 1])
    budgets = [r["budget"] for r in rows if r["budget"] is not None]
    std = 0.0
    if len(budgets) >= 2:
        mean = sum(budgets) / len(budgets)
        std = (sum((b - mean) ** 2 for b in budgets) / len(budgets)) ** 0.5
    if std > OSCILLATION_STD and alternations > OSCILLATION_ALTERNATE:
        set_setting("oscillation_damping_until", str(time.time() + DAMPING_SECONDS), dbx)
        log_event("oscillation_damping", std=round(std, 4),
                  alternations=alternations,
                  msg="振荡检测触发：κ 强制 0.1 持续 30 分钟")
        return True
    return False


# ---------- pred_error 24h 累积增量 ----------

def pe_24h_accum(database: Database | None = None) -> float:
    """归一化前的 24h 累积增量（单边最大值）。"""
    dbx = database or db
    raw = get_setting("pe_24h_accum", dbx)
    try:
        data = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return 0.0
    if data.get("date") != _today():
        return 0.0
    return float(max(data.get("edges", {}).values()) or 0.0)


def note_pe_accum(edge_id: int, delta: float, database: Database | None = None) -> None:
    """累积时记账（accumulate_pred_error 调用；按日清账）。"""
    dbx = database or db
    raw = get_setting("pe_24h_accum", dbx)
    try:
        data = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        data = {}
    if data.get("date") != _today():
        data = {"date": _today(), "edges": {}}
    edges = data.setdefault("edges", {})
    key = str(edge_id)
    edges[key] = round(edges.get(key, 0.0) + delta, 4)
    set_setting("pe_24h_accum", json.dumps(data, ensure_ascii=False), dbx)


# ---------- 每日维护（tick 钩子） ----------

def tick_maintenance(elapsed_seconds: float, database: Database | None = None) -> None:
    """挂进生命循环的维护钩子：跨天时执行归一化/衰减/归档/周报。"""
    dbx = database or db
    last = get_setting("maintenance_date", dbx)
    today = _today()
    if last == today:
        return
    set_setting("maintenance_date", today, dbx)

    from app.decisions.simulate import normalize_pred_errors
    normalize_pred_errors(dbx)

    from app.life.stimulus import decay_weekly
    if _now().weekday() == 0:  # 每周一衰减一次（R20′：每 7 天 count×0.5）
        decay_weekly(dbx)

    archive_life_log(dbx)
    maybe_weekly_report(dbx)
    log_event("daily_maintenance", date=today, msg="每日维护完成")


def archive_life_log(database: Database | None = None) -> None:
    """life_log 7 天明细保留；更早日聚合为日摘要入 life_log_archive 后清理。"""
    dbx = database or db
    conn = dbx.conn()
    cutoff = (_now() - timedelta(days=LIFE_LOG_KEEP_DAYS)).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT id, tick, event, detail, ts FROM life_log WHERE ts < ?"
        " ORDER BY ts", (cutoff,)).fetchall()
    if not rows:
        return
    by_day: dict[str, list] = {}
    for r in rows:
        day = r["ts"][:10]
        by_day.setdefault(day, []).append(r)
    for day, items in by_day.items():
        events = {}
        for r in items:
            events[r["event"]] = events.get(r["event"], 0) + 1
        conn.execute(
            "INSERT INTO life_log_archive (day, summary, created_at) VALUES (?,?,?)",
            (day, json.dumps({"rows": len(items), "events": events},
                             ensure_ascii=False), _now().isoformat(timespec="seconds")))
    conn.execute("DELETE FROM life_log WHERE ts < ?", (cutoff,))
    conn.commit()
    log_event("life_log_archive", days=len(by_day), rows=len(rows),
              msg="life_log 归档（7 天明细 + 更早日摘要）")


def maybe_weekly_report(database: Database | None = None) -> None:
    """每周一次：平均 budget / p_self 终值 / CONFRONT 次数 / 降级次数 → 归档+weekly.md。"""
    dbx = database or db
    last = get_setting("weekly_report_date", dbx)
    week = _now().strftime("%Y-W%W")
    if last == week:
        return
    set_setting("weekly_report_date", week, dbx)
    conn = dbx.conn()
    budgets, p_selfs = [], []
    for r in conn.execute(
            "SELECT detail FROM life_log WHERE event='tick' ORDER BY id DESC LIMIT 20000"):
        try:
            d = json.loads(r["detail"] or "{}")
            if "budget" in d:
                budgets.append(float(d["budget"]))
            if "p_self" in d:
                p_selfs.append(float(d["p_self"]))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    avg_b = round(sum(budgets) / len(budgets), 3) if budgets else None
    p_final = round(p_selfs[-1], 3) if p_selfs else None
    confronts = conn.execute(
        "SELECT COUNT(*) c FROM decision_log WHERE action='CONFRONT'").fetchone()["c"]
    degradations = conn.execute(
        "SELECT COUNT(*) c FROM life_log WHERE event='degradation_level'"
    ).fetchone()["c"]
    report = {"week": week, "avg_budget": avg_b, "p_self_final": p_final,
              "confront_count": confronts, "degradation_count": degradations}
    conn.execute(
        "INSERT INTO life_log_archive (day, summary, created_at) VALUES (?,?,?)",
        (week, json.dumps(report, ensure_ascii=False),
         _now().isoformat(timespec="seconds")))
    conn.commit()
    try:
        from app.config import LOG_DIR
        weekly = LOG_DIR / "weekly.md"
        line = (f"\n## {week}\n- 平均 budget：{avg_b}\n- p_self 终值：{p_final}\n"
                f"- CONFRONT 次数：{confronts}\n- 降级次数：{degradations}\n")
        with weekly.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        log.exception("周报文件写入失败（不影响主流程）")
    log_event("weekly_report", **report, msg=f"周报摘要已生成：{week}")
