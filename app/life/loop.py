"""R1 生命循环底座：60s tick（绝对时间校准）+ 单事务 DB 写 + 一次刷内存。

- R0#4：target_time 绝对时间；恢复/衰减量按实际 elapsed_seconds 换算
- R19：tick 全部 DB 读写单事务 → 一次 refresh()；run_once 与循环共用 asyncio 锁
- R17 骨架：写前校验（write_checked）/ 存量越界钳回 / 检查点环形缓冲
- R16：每 5 tick 静默决策模拟；silent_ticks>10 → p_self 回归增益 ×2
- 钩子注册：R2 主观漂移 / R4 图生长与边衰减 / R5 学习扫描 / 记忆卫生（后续阶段接入）
"""
import asyncio
import json
import time
from datetime import datetime, timezone

from app.config import settings
from app.db import db
from app.emotion.clock import accumulate_offline
from app.emotion.state import EmotionSystem
from app.life.planning import simulate_silent_planning
from app.life.self_model import SILENT_GAIN_TICKS, step_regression
from app.life.state import (GlobalCognitiveState, VALID_DOMAINS, save_checkpoint,
                            validate_domain, write_checked)
from app.logging_setup import get_logger, log_event
from app.proactive.settings import get_setting, set_setting

log = get_logger("life.loop")

MAX_ELAPSED_SECONDS = 600.0  # 停机重启后单 tick 折算封顶（10 分钟当量）


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class LifeLoop:
    def __init__(self, database=None):
        self.db = database or db
        self.state = GlobalCognitiveState(self.db)
        self.tick_seconds = settings.life_tick_seconds
        self._task: asyncio.Task | None = None
        self._run_lock = asyncio.Lock()
        self._hooks: list[tuple[str, object]] = []
        self._tick_no = 0
        self._last_tick_ts: float | None = None
        try:
            self._silent_ticks = int(get_setting("silent_ticks", self.db) or 0)
        except (TypeError, ValueError):
            self._silent_ticks = 0

    # ---------- 生命周期 ----------

    def register_hook(self, name: str, fn) -> None:
        """后续阶段（R2/R4/R5/记忆卫生）把维护逻辑挂进 tick。fn(elapsed_seconds)。"""
        self._hooks.append((name, fn))
        log.info("tick 钩子已注册：%s", name)

    async def start(self) -> None:
        if not settings.life_loop_enabled:
            log.info("生命循环未启用（LIFE_LOOP_ENABLED=false），跳过后台 tick")
            return
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._loop())
            log.info("生命循环启动：每 %.0f 秒一个 tick", self.tick_seconds)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            log.info("生命循环已停止")

    async def run_once(self, tick: int | None = None) -> dict:
        """手动跑一个 tick（测试/接口用），与后台循环共用同一把锁。"""
        async with self._run_lock:
            return await self._tick(tick)

    # ---------- 循环与 tick ----------

    async def _loop(self) -> None:
        while True:
            start = time.time()
            try:
                async with self._run_lock:
                    await self._tick()
            except Exception:
                log.exception("tick 异常（循环继续）")
            # R0#4 绝对时间校准：target_time = 本周期起点 + 60，不累积漂移
            await asyncio.sleep(max(0.0, start + self.tick_seconds - time.time()))

    async def _tick(self, tick: int | None = None) -> dict:
        now = time.time()
        self._tick_no = (self._tick_no + 1) if tick is None else tick
        tick_no = self._tick_no
        if self._last_tick_ts is None:
            self._last_tick_ts = now
        elapsed = min(max(0.0, now - self._last_tick_ts), MAX_ELAPSED_SECONDS)
        self._last_tick_ts = now

        # 静默计数：距用户最后消息 ≥ 一个 tick → 静默 +1；有对话 → 清零
        self._silent_ticks = self._compute_silent()
        set_setting("silent_ticks", str(self._silent_ticks), self.db)

        conn = self.db.conn()
        detail_full: dict = {}
        conn.execute("BEGIN IMMEDIATE")
        try:
            # 情绪衰减（按实际间隔折算）
            EmotionSystem(self.db).decay_seconds(elapsed)
            # 内部时钟积分：孤独离线漂移（离线>2h 才累积）
            accumulate_offline(self.db)
            # R17 存量越界钳回（保不变量）
            self._clamp_domains(conn)
            # 内稳态 tick 回充（+0.01/分钟当量，封顶 1.0；R11′ 深/短轮成本另行接入）
            row = conn.execute("SELECT budget FROM homeostatic_state WHERE id=1").fetchone()
            if row:
                recharge_target = min(1.0, row["budget"] + 0.01 * elapsed / 60.0)
                write_checked(self.db, "homeostatic_state", "budget", "budget",
                              recharge_target)
            # p_self 锚点回归（R13′ 基础；silent_ticks>10 → 回归增益 ×2，R16）
            gain = 2.0 if self._silent_ticks > SILENT_GAIN_TICKS else 1.0
            step_regression(self.db, gain_multiplier=gain)
            # life_log + 能力计数 + 心跳戳（同事务；明细 = 全量子系统真实数据）
            from app.life.state import full_snapshot
            detail_full = full_snapshot(self.db)
            detail_full["elapsed"] = round(elapsed, 2)
            detail_full["tick"] = tick_no
            conn.execute(
                "INSERT INTO life_log (tick, event, detail, ts) VALUES (?,?,?,?)",
                (tick_no, "tick", json.dumps(detail_full, ensure_ascii=False), _now()))
            conn.execute(
                "INSERT INTO capability_usage (capability, count) VALUES ('life_tick',1)"
                " ON CONFLICT(capability) DO UPDATE SET count=count+1")
            conn.execute("UPDATE homeostatic_state SET last_tick_at=?, updated_at=? WHERE id=1",
                         (_now(), _now()))
            conn.execute("COMMIT")
        except Exception:
            conn.rollback()
            log.exception("tick 事务失败，已整体回滚（R19）")
            return {"tick": tick_no, "ok": False}

        # 单事务完成后一次刷新内存（R19）
        self.state.refresh()

        # 后续阶段钩子（事务外执行，允许异步）：主观漂移(R2)/图生长(R4)/
        # 学习扫描(R5)/每日维护(R20′)
        for name, fn in self._hooks:
            try:
                result = fn(elapsed)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                log.exception("tick 钩子失败：%s", name)

        snap = self.state.snapshot()

        # 静默决策模拟（R16，每 5 tick，零 LLM）
        silent = None
        if tick_no % settings.silent_planning_interval_ticks == 0:
            try:
                silent = simulate_silent_planning(self.db, self.state, tick=tick_no)
            except Exception:
                log.exception("静默决策模拟失败（tick 继续）")

        # 检查点（R17，每 10 tick，环形缓冲）
        if tick_no % settings.checkpoint_interval_ticks == 0:
            try:
                save_checkpoint(self.db, tick=tick_no)
            except Exception:
                log.exception("检查点写入失败（tick 继续）")

        log_event(
            "tick", tick=tick_no, elapsed=round(elapsed, 2),
            budget=round(snap["budget"], 4), p_self=round(snap["p_self"], 4),
            velocity=round(snap["velocity"], 5),
            valence=round(snap["valence"], 3), arousal=round(snap["arousal"], 3),
            loneliness=round(snap["loneliness"], 3), dominant=snap["dominant"],
            silent_ticks=self._silent_ticks, top_pe=snap["top_pe_edge"],
            snapshot=detail_full,
            msg=(f"tick {tick_no} 完成（实际间隔 {elapsed:.1f}s | "
                 f"budget={snap['budget']:.4f} p_self={snap['p_self']:.4f} "
                 f"孤独={snap['loneliness']:.3f} 主导情绪={snap['dominant']}）"),
        )
        return {"tick": tick_no, "ok": True, "elapsed": round(elapsed, 2),
                "budget": snap["budget"], "silent": silent}

    # ---------- 内部 ----------

    def _compute_silent(self) -> int:
        last = get_setting("last_user_message_at", self.db)
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
                now_dt = datetime.now(timezone.utc).astimezone()
                if (now_dt - last_dt).total_seconds() < self.tick_seconds:
                    return 0
            except ValueError:
                pass
        return self._silent_ticks + 1

    def _clamp_domains(self, conn) -> None:
        """R17：存量越界值钳回合法域 + ALERT（写路径的越界拒写由 write_checked 负责）。"""
        for table, col, field in (
                ("homeostatic_state", "budget", "budget"),
                ("self_model", "p_self", "p_self"),
                ("emotion_state", "valence", "valence"),
                ("emotion_state", "arousal", "arousal")):
            row = conn.execute(f"SELECT {col} FROM {table} WHERE id=1").fetchone()
            if row is None:
                continue
            v = float(row[col])
            lo, hi = VALID_DOMAINS[field]
            if not (lo <= v <= hi):
                fixed = min(hi, max(lo, v))
                conn.execute(f"UPDATE {table} SET {col}=? WHERE id=1", (round(fixed, 6),))
                log_event("state_alert", field=field, was=round(v, 4),
                          clamped=round(fixed, 4),
                          msg=f"R17 存量越界钳回：{field}={v} → {fixed}")
        try:
            from app.emotion.clock import loneliness
            lon = loneliness(self.db)["total"]
            if not validate_domain("loneliness", lon):
                log_event("state_alert", field="loneliness", was=round(lon, 4),
                          msg="R17 孤独感越界（只告警，值由时钟公式钳制）")
        except Exception:
            pass
