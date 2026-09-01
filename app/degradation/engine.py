"""三级动态降级状态机（R18′）：L1a 静默重试 / L1b 句法森林回声 / L2 静默。

反偷懒四机制：
1. 超时动态递减（60s 起，每失败 −5s，下限 10s；只作用于对话级 LLM 调用）
2. 恢复后温度补偿 +0.2 持续 5 轮（上限 1.0）
3. 降级时长只侵蚀 connection_reliability（每分钟 −0.01，下限 0.3，
   只进生成参数包 fluency_penalty，绝不碰 p_self）
4. 无惯性开关：每个请求强制真实尝试 ≤2 次，后台探测永不停止

六盲区闭环：
1. 恢复乒乓 → 回主路径锁定 ≥5 分钟；锁定期失败只走 L1a；连续 3 次失败才解锁降级
2. 主动自杀触发 → 降级 ≥L1b 时心跳熔断；恢复后主动日预算 +3 条（仅当天）
3. 负面归因级联 → L1b/L2 冻结情绪感知/冲突计数；恢复首轮情绪回降级前快照
4. 异步任务死锁 → 任务置 suspended（partial_context/last_response），恢复续传，24h 淘汰
5. 降级感知缺失 → 句法森林 [INTENT_TAG] 意图回响（forest.py）
6. 记忆空洞断层 → 降级文本 is_degraded=1，recall 默认过滤，UI 差异化渲染
"""
import asyncio
import json
import time
from datetime import datetime, timezone

from app.config import settings
from app.db import Database, db
from app.logging_setup import get_logger, log_event
from app.proactive.settings import get_setting, set_setting

log = get_logger("degradation")

LEVELS = ("main", "L1a", "L1b", "L2")

RECOVERY_LOCK_SECONDS = 300      # 恢复锁定 ≥5 分钟
UNLOCK_FAILURES = 3              # 连续 3 次失败才解锁降级
PROBE_INTERVAL_L1B = 30          # L1b 每 30s 探测
PROBE_INTERVAL_L2 = 300          # L2 每 5min 探测
L1B_TO_L2_SECONDS = 600          # L1b 连续 10 分钟探测失败 → L2
L2_VERIFY_ROUNDS = 2             # L2 → L1b 两轮验证对话
TEMP_COMP_ROUNDS = 5             # 恢复后温度补偿持续轮数
TEMP_COMP_DELTA = 0.2            # 温度上浮
TIMEOUT_START = None             # 起于 settings.llm_timeout
TIMEOUT_STEP = 5.0               # 每失败 −5s
TIMEOUT_FLOOR = 10.0             # 下限
RELIABILITY_FLOOR = 0.3          # connection_reliability 下限
RELIABILITY_DROP_PER_MIN = 0.01
PROACTIVE_BONUS = 3              # 恢复当日主动日预算补偿条数
SUSPENDED_TTL_HOURS = 24


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _now_ts() -> float:
    return time.time()


class DegradedError(Exception):
    """用户请求在降级层被拦截：level ∈ {L1b, L2}。"""

    def __init__(self, level: str):
        super().__init__(f"degraded:{level}")
        self.level = level


class DegradationEngine:
    def __init__(self, database: Database | None = None, llm=None):
        self.db = database or db
        self.llm = llm                    # DeepSeekClient（探测用；请求级调用由调用方传入）
        self._task: asyncio.Task | None = None
        self._consecutive_failures = 0
        self.on_recovered = []            # 恢复回调（tasks_service 续传等）
        self.on_entered_degraded = []     # 进入降级回调（心跳熔断等）

    # ---------- 状态读取/持久化 ----------

    def get_level(self) -> str:
        lv = get_setting("degradation_level", self.db) or "main"
        return lv if lv in LEVELS else "main"

    def _set_level(self, level: str, extra: dict | None = None) -> None:
        set_setting("degradation_level", level, self.db)
        if extra:
            for k, v in extra.items():
                set_setting(k, str(v), self.db)
        log_event("degradation_level", level=level,
                  msg=f"降级级切换 → {level}")

    def is_degraded(self) -> bool:
        return self.get_level() in ("L1b", "L2")

    def emotion_frozen(self) -> bool:
        """盲区三：L1b/L2 期间情绪感知/主观更新/冲突计数冻结。"""
        return self.is_degraded()

    def connection_reliability(self) -> float:
        """降级时长侵蚀连接置信度（只影响流畅度，不碰 p_self）。"""
        entered = get_setting("deg_entered_at", self.db)
        if not entered or self.get_level() == "main":
            # 主路径且无未结锁定 → 完全可靠；恢复锁定期间保留低值（P2-4）
            return 1.0
        try:
            dt = datetime.fromisoformat(entered)
            minutes = max(0.0, (_now_ts() - dt.timestamp()) / 60.0)
        except (ValueError, OSError):
            return 1.0
        return round(max(RELIABILITY_FLOOR, 1.0 - RELIABILITY_DROP_PER_MIN * minutes), 3)

    def timeout_for_request(self) -> float:
        """反偷懒 1：动态超时递减（只作用于对话级 LLM 调用）。"""
        base = settings.llm_timeout
        return max(TIMEOUT_FLOOR, base - TIMEOUT_STEP * self._consecutive_failures)

    def temp_for(self, base_temp: float) -> float:
        """反偷懒 2：恢复后温度补偿 +0.2（≤1.0），持续 5 轮。"""
        try:
            rounds = int(get_setting("temp_comp_rounds", self.db) or 0)
        except (TypeError, ValueError):
            rounds = 0
        if rounds <= 0:
            return base_temp
        return min(1.0, base_temp + TEMP_COMP_DELTA)

    # ---------- 请求守卫（api 主路径唯一入口） ----------

    async def guard(self, user_text: str, call) -> str:
        """执行一次用户请求的 LLM 生成。成功返回 reply 文本。

        - L1b：抛 DegradedError('L1b')，api 改走句法森林
        - L2：抛 DegradedError('L2')，api 返回纯状态码（无 message）
        """
        level = self.get_level()
        if level == "L2":
            # P2-12：L2 期间用户消息 = 验证轮（尝试真实 LLM）
            try:
                reply = await self._attempt(call)
                self._l2_verify_success()
                return reply
            except Exception:
                raise DegradedError("L2")
        if level == "L1b":
            # 无惯性开关：每个请求强制真实尝试（≤2 次），成功即切回主路径
            try:
                reply = await self._attempt(call)
                self._recover_from_l1b()
                return reply
            except Exception:
                raise DegradedError("L1b")
        # main 路径
        try:
            reply = await self._attempt(call)
            self._on_success()
            return reply
        except Exception:
            self._consecutive_failures += 1
            if self._in_recovery_lock():
                # 盲区一：锁定期间失败只算解锁计数，不降级
                self._lock_failure()
                raise
            self._enter_l1b()
            raise DegradedError("L1b")

    async def _attempt(self, call):
        """L1a：单次失败 → 立即重试（0 延迟）；两次都失败才上抛。"""
        last: Exception | None = None
        for i in range(2):
            try:
                return await call()
            except Exception as exc:
                last = exc
                if i == 0:
                    log_event("l1a_retry", attempt=i + 1,
                              msg=f"L1a 静默重试（第 {i + 1} 次失败后立即重试）")
        raise last if last is not None else RuntimeError("unknown failure")

    # ---------- 状态迁移 ----------

    def _enter_l1b(self) -> None:
        self._snapshot_emotion()
        self._set_level("L1b", {
            "deg_entered_at": _now(),
            "l1b_probe_fail_start": str(_now_ts()),
        })
        self._consecutive_failures = 0
        for cb in list(self.on_entered_degraded):
            try:
                cb("L1b")
            except Exception:
                log.exception("进入降级回调失败")

    def force_l2(self, reason: str = "") -> None:
        """句法森林过载/无锚点 → 提前进入 L2（仍保留探测恢复）。"""
        if self.get_level() == "L2":
            return
        self._set_level("L2", {"deg_entered_at": _now()})
        log_event("force_l2", reason=reason, msg=f"提前进入 L2：{reason}")

    def _recover_from_l1b(self) -> None:
        self._set_level("main", {
            "recovery_lock_until": str(_now_ts() + RECOVERY_LOCK_SECONDS),
            "temp_comp_rounds": TEMP_COMP_ROUNDS,
            "lock_failures": "0",
            "post_recovery_probe_success": "0",
        })
        self._consecutive_failures = 0
        set_setting("pending_emotion_restore", "true", self.db)
        self._proactive_bonus()
        for cb in list(self.on_recovered):
            try:
                cb()
            except Exception:
                log.exception("恢复回调失败")
        log_event("recovered", from_level="L1b", lock_seconds=RECOVERY_LOCK_SECONDS,
                  msg="L1b → 主路径，进入恢复锁定 + 温度补偿")

    def _l2_verify_success(self) -> None:
        try:
            remaining = int(get_setting("l2_verify_remaining", self.db) or 0)
        except (TypeError, ValueError):
            remaining = 0
        remaining -= 1
        if remaining <= 0:
            self._recover_from_l1b()
            log_event("l2_recovery", verified=True,
                      msg="L2 两轮验证对话成功，正式切回主路径")
        else:
            set_setting("l2_verify_remaining", str(remaining), self.db)
            self._set_level("L1b", {})
            log_event("l2_recovery", verified=False, remaining=remaining,
                      msg=f"L2 验证轮成功（剩 {remaining} 轮），先入 L1b")

    def _on_success(self) -> None:
        self._consecutive_failures = 0
        try:
            rounds = int(get_setting("temp_comp_rounds", self.db) or 0)
        except (TypeError, ValueError):
            rounds = 0
        if rounds > 0:
            set_setting("temp_comp_rounds", str(rounds - 1), self.db)

    def _in_recovery_lock(self) -> bool:
        try:
            until = float(get_setting("recovery_lock_until", self.db) or 0)
        except (TypeError, ValueError):
            return False
        return _now_ts() < until

    def _lock_failure(self) -> None:
        try:
            n = int(get_setting("lock_failures", self.db) or 0) + 1
        except (TypeError, ValueError):
            n = 1
        set_setting("lock_failures", str(n), self.db)
        if not self._in_recovery_lock() and n >= UNLOCK_FAILURES:
            self._enter_l1b()

    # ---------- 盲区二：主动熔断 + 恢复补偿 ----------

    def proactive_blocked(self) -> bool:
        return self.get_level() in ("L1b", "L2")

    def proactive_bonus_quota(self, base: int) -> int:
        try:
            day = get_setting("proactive_bonus_date", self.db)
        except Exception:
            day = None
        today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
        return base + PROACTIVE_BONUS if day == today else base

    def _proactive_bonus(self) -> None:
        today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
        set_setting("proactive_bonus_date", today, self.db)
        log_event("proactive_bonus", date=today, bonus=PROACTIVE_BONUS,
                  msg=f"恢复后主动日预算 +{PROACTIVE_BONUS} 条（仅限当天）")

    # ---------- 盲区三：情绪冻结与恢复 ----------

    def _snapshot_emotion(self) -> None:
        row = self.db.conn().execute(
            "SELECT * FROM emotion_state WHERE id=1").fetchone()
        if row is None:
            self.db.conn().execute(
                "INSERT INTO emotion_state (id, updated_at) VALUES (1, ?)", (_now(),))
            self.db.conn().commit()
            row = self.db.conn().execute(
                "SELECT * FROM emotion_state WHERE id=1").fetchone()
        snap = {k: row[k] for k in (
            "valence", "arousal", "dominance", "joy", "sadness", "anger",
            "fear", "surprise", "disgust", "anticipation", "trust", "dominant")}
        set_setting("pre_deg_emotion", json.dumps(snap, ensure_ascii=False), self.db)

    def restore_emotion_if_pending(self) -> bool:
        """恢复首轮：情绪回退到降级前快照（盲区三）。"""
        if get_setting("pending_emotion_restore", self.db) != "true":
            return False
        raw = get_setting("pre_deg_emotion", self.db)
        if not raw:
            set_setting("pending_emotion_restore", "false", self.db)
            return False
        try:
            snap = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            set_setting("pending_emotion_restore", "false", self.db)
            return False
        cols = ", ".join(f"{k}=?" for k in snap)
        self.db.conn().execute(
            f"UPDATE emotion_state SET {cols}, updated_at=? WHERE id=1",
            (*snap.values(), _now()))
        self.db.conn().commit()
        set_setting("pending_emotion_restore", "false", self.db)
        log_event("emotion_restored", snapshot=snap.get("dominant"),
                  msg="恢复首轮：情绪回退到降级前快照")
        return True

    # ---------- 探测循环 ----------

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._loop())
            log.info("降级探测循环已启动")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._probe_interval())
            try:
                await self._probe_once()
            except Exception:
                log.exception("降级探测异常（循环继续）")

    def _probe_interval(self) -> float:
        level = self.get_level()
        if level == "L2":
            return PROBE_INTERVAL_L2
        if level == "L1b" or self._in_recovery_lock():
            return PROBE_INTERVAL_L1B
        return 60.0

    async def _probe_once(self) -> None:
        if self.llm is None:
            return
        level = self.get_level()
        ok = False
        started = _now_ts()
        try:
            result = await self.llm.probe(timeout=5.0)
            ok = bool(result.get("ok"))
        except Exception:
            ok = False
        # 探测结果每次都留痕（成功也写——完整运维数据）
        log_event("probe", ok=ok, level=level,
                  elapsed_ms=round((_now_ts() - started) * 1000),
                  reliability=self.connection_reliability(),
                  msg=f"降级探测：{level} {'成功' if ok else '失败'}")
        if ok:
            if level == "L1b":
                self._recover_from_l1b()
            elif level == "L2":
                self._set_level("L1b", {"l2_verify_remaining": L2_VERIFY_ROUNDS})
                log_event("l2_probe_ok", msg="L2 探测成功，先入 L1b 等待两轮验证对话")
            elif level == "main" and self._in_recovery_lock():
                try:
                    n = int(get_setting("post_recovery_probe_success", self.db) or 0) + 1
                except (TypeError, ValueError):
                    n = 1
                set_setting("post_recovery_probe_success", str(n), self.db)
                if n >= 3:
                    set_setting("deg_entered_at", "", self.db)
                    log_event("reliability_reset", probes=n,
                              msg="恢复锁定后连续 3 次探测成功 → connection_reliability 重置 1.0")
            return
        # 探测失败
        if level == "L1b":
            start = get_setting("l1b_probe_fail_start", self.db)
            try:
                since = _now_ts() - float(start)
            except (TypeError, ValueError):
                since = 0.0
            if since >= L1B_TO_L2_SECONDS:
                self._set_level("L2", {"l2_verify_remaining": L2_VERIFY_ROUNDS})
                log_event("l1b_to_l2", fail_seconds=round(since),
                          msg="L1b 连续 10 分钟探测失败 → L2 静默")


# 全局单例（llm 由 main lifespan 注入）
degradation = DegradationEngine()
