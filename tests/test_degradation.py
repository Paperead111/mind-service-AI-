"""R18′ 三级降级 + 句法森林测试。

覆盖：L1a 透明重试 / L1b 森林回声 / L2 纯状态码 / 恢复锁定 / 温度补偿 /
动态超时 / connection_reliability / 情绪冻结与恢复 / 主动熔断与补偿配额 /
意图探测 / 森林状态锚定与长度 / 兜底提前 L2。
运行：python -m pytest tests/test_degradation.py -q
"""
import asyncio
import json
import time
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.db import Database
from app.degradation.engine import (DegradedError, DegradationEngine,
                                    L2_VERIFY_ROUNDS)
from app.degradation.forest import generate as forest_generate
from app.degradation.intent import detect_intent
from app.proactive.settings import set_setting

TEST_DB = Path(r"D:\DeepSeek Harness\mind-service\.tmp\test_degradation.db")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class DegradationTestCase(unittest.TestCase):
    def setUp(self):
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(TEST_DB) + suffix)
            if p.exists():
                p.unlink()
        self.db = Database(TEST_DB)
        self.eng = DegradationEngine(self.db)

    def tearDown(self):
        self.db.close()
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(TEST_DB) + suffix)
            if p.exists():
                p.unlink()


class FailingCall:
    """可编程 LLM 调用：前 fail_times 次失败，之后返回 ok_text。"""

    def __init__(self, fail_times=0, ok_text="好了", exc=None):
        self.fail_times, self.calls, self.ok_text = fail_times, 0, ok_text
        self.exc = exc or RuntimeError("llm down")

    async def __call__(self):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc
        return self.ok_text


class TestIntent(unittest.TestCase):
    def test_intent_classes(self):
        self.assertEqual(detect_intent("今天天气怎么样？"), "question")
        self.assertEqual(detect_intent("我好开心啊哈哈"), "positive_share")
        self.assertEqual(detect_intent("我太难过了"), "negative_share")
        self.assertEqual(detect_intent("我去超市了"), "statement")


class TestForest(DegradationTestCase):
    def test_state_anchoring_budget_low(self):
        state = {"budget": 0.2, "p_self": 0.85, "top_pe_edge": {"pred_error": 0.3},
                 "silent_ticks": 0}
        out = forest_generate("随便说点什么吧", state, self.db)
        body = out["text"].rstrip("。")
        self.assertLessEqual(len(body), 8)
        self.assertTrue(any(w in body for w in ("累", "空", "紧")),
                        f"状态锚定失效：{out['text']}")

    def test_state_anchoring_stable(self):
        state = {"budget": 0.7, "p_self": 0.85, "top_pe_edge": {"pred_error": 0.3},
                 "silent_ticks": 0}
        found = False
        for _ in range(10):
            out = forest_generate("随便说点什么吧", state, self.db)
            if out["fallback"]:  # 语义指纹去重耗尽 → 兜底，不校验锚定
                continue
            body = out["text"].rstrip("。")
            self.assertTrue(any(w in body for w in ("稳", "平", "松")),
                            f"稳定态锚定失效：{out['text']}")
            found = True
            break
        self.assertTrue(found, "未产出任何非兜底输出")

    def test_output_length_limit(self):
        state = {"budget": 0.2, "p_self": 0.4, "top_pe_edge": {"pred_error": 0.9},
                 "silent_ticks": 3}
        for _ in range(20):
            body = forest_generate("你觉得我应该学点什么？", state, self.db)["text"].rstrip("。")
            self.assertLessEqual(len(body), 8)

    def test_echo_anchor_from_message(self):
        from app.degradation.forest import extract_echo
        self.assertEqual(extract_echo("我想学点新的东西", self.db), "新的东西")
        self.assertEqual(extract_echo("？？？", self.db), None)  # 无可提取实词

    def test_forest_overload_forces_l2(self):
        import app.degradation.forest as forest
        set_setting("forest_fallback_streak", "0", self.db)
        state = {"budget": 0.7, "p_self": 0.85, "top_pe_edge": {"pred_error": 0.3},
                 "silent_ticks": 0}
        with unittest.mock.patch.object(forest, "REGEN_LIMIT", -1):
            # 三轮兜底 → 连续 3 次 → 提前 L2
            for i in range(1, 4):
                out = forest_generate("随便说点什么吧", state, self.db)
                self.assertTrue(out["fallback"])
        self.assertEqual(self.eng.get_level(), "L2")


class TestEngine(DegradationTestCase):
    def test_l1a_transparent_retry(self):
        call = FailingCall(fail_times=1)
        reply = asyncio.run(self.eng.guard("嗨", call))
        self.assertEqual(reply, "好了")
        self.assertEqual(call.calls, 2)
        self.assertEqual(self.eng.get_level(), "main")

    def test_two_failures_enter_l1b(self):
        call = FailingCall(fail_times=2)
        with self.assertRaises(DegradedError) as ctx:
            asyncio.run(self.eng.guard("嗨", call))
        self.assertEqual(ctx.exception.level, "L1b")
        self.assertEqual(self.eng.get_level(), "L1b")
        self.assertTrue(self.eng.emotion_frozen())

    def test_l1b_user_request_success_recovers(self):
        self.eng._enter_l1b()
        call = FailingCall(fail_times=0, ok_text="回来了")
        reply = asyncio.run(self.eng.guard("在吗", call))
        self.assertEqual(reply, "回来了")
        self.assertEqual(self.eng.get_level(), "main")
        self.assertTrue(self.eng._in_recovery_lock())
        self.assertEqual(self.eng.temp_for(0.8), 1.0)  # +0.2 封顶 1.0

    def test_recovery_lock_blocks_redegradation(self):
        self.eng._enter_l1b()
        self.eng._recover_from_l1b()  # 进入锁定
        call = FailingCall(fail_times=2)
        with self.assertRaises(RuntimeError):  # 锁定期失败只上抛，不降级
            asyncio.run(self.eng.guard("嗨", call))
        self.assertEqual(self.eng.get_level(), "main")

    def test_lock_unlock_after_three_failures(self):
        self.eng._enter_l1b()
        self.eng._recover_from_l1b()
        set_setting("recovery_lock_until", "0", self.db)  # 锁已过期
        for _ in range(3):
            self.eng._lock_failure()
        self.assertEqual(self.eng.get_level(), "L1b")

    def test_dynamic_timeout_decreases(self):
        base = self.eng.timeout_for_request()
        self.eng._consecutive_failures = 3
        self.assertEqual(self.eng.timeout_for_request(), base - 15)

    def test_connection_reliability_floor(self):
        self.eng._enter_l1b()
        set_setting("deg_entered_at",
                    (datetime.now(timezone.utc).astimezone()
                     - timedelta(hours=5)).isoformat(timespec="seconds"), self.db)
        self.assertEqual(self.eng.connection_reliability(), 0.3)

    def test_l2_verification_rounds(self):
        self.eng._set_level("L2", {"l2_verify_remaining": L2_VERIFY_ROUNDS})
        call = FailingCall(fail_times=0, ok_text="验证轮")
        self.assertEqual(asyncio.run(self.eng.guard("在吗", call)), "验证轮")
        # 一轮验证成功 → 仍 L1b（还剩 1 轮）
        self.assertEqual(self.eng.get_level(), "L1b")

    def test_l2_failure_raises_degraded_l2(self):
        self.eng._set_level("L2", {"l2_verify_remaining": L2_VERIFY_ROUNDS})
        call = FailingCall(fail_times=2)
        with self.assertRaises(DegradedError) as ctx:
            asyncio.run(self.eng.guard("在吗", call))
        self.assertEqual(ctx.exception.level, "L2")

    def test_emotion_restore_snapshot(self):
        from app.emotion.state import EmotionSystem
        EmotionSystem(self.db).state()  # 播种 emotion_state 单行
        self.db.conn().execute("UPDATE emotion_state SET valence=-0.4, anger=0.5 WHERE id=1")
        self.db.conn().commit()
        self.eng._enter_l1b()          # 快照此刻情绪
        self.db.conn().execute("UPDATE emotion_state SET valence=-0.9, anger=0.9 WHERE id=1")
        self.db.conn().commit()        # 降级期间被外部改变
        set_setting("pending_emotion_restore", "true", self.db)
        self.assertTrue(self.eng.restore_emotion_if_pending())
        row = self.db.conn().execute(
            "SELECT valence, anger FROM emotion_state WHERE id=1").fetchone()
        self.assertAlmostEqual(row["valence"], -0.4, places=5)
        self.assertAlmostEqual(row["anger"], 0.5, places=5)

    def test_proactive_blocked_and_bonus(self):
        self.assertFalse(self.eng.proactive_blocked())
        self.eng._enter_l1b()
        self.assertTrue(self.eng.proactive_blocked())
        self.eng._recover_from_l1b()
        self.assertFalse(self.eng.proactive_blocked())
        today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
        self.assertEqual(self.eng.proactive_bonus_quota(20), 23)
        set_setting("proactive_bonus_date", "2000-01-01", self.db)
        self.assertEqual(self.eng.proactive_bonus_quota(20), 20)

    def test_suspended_tasks_resume_and_expire(self):
        """验收 #26：降级期间任务 suspended → 恢复续传；>24h 淘汰。"""
        from app.service.tasks import TaskService
        svc = TaskService(database=self.db)
        now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        self.db.conn().execute(
            "INSERT INTO tasks (id, dedupe_key, status, input, created_at)"
            " VALUES ('t1','d1','suspended','{}',?)", (now,))
        self.db.conn().execute(
            "INSERT INTO tasks (id, dedupe_key, status, input, created_at)"
            " VALUES ('t2','d2','suspended','{}',?)",
            ((datetime.now(timezone.utc).astimezone()
              - timedelta(hours=30)).isoformat(timespec="seconds"),))
        self.db.conn().commit()
        resumed = svc.resume_suspended()
        self.assertEqual(resumed, 1)
        t1 = self.db.conn().execute("SELECT status FROM tasks WHERE id='t1'").fetchone()
        self.assertEqual(t1["status"], "pending")
        t2 = self.db.conn().execute("SELECT status FROM tasks WHERE id='t2'").fetchone()
        self.assertEqual(t2["status"], "failed")

    def test_oscillation_detection(self):
        """验收 #28：近 10 轮 budget 波动 + SILENCE/LOOKUP 交替 → 阻尼触发。"""
        from app.life.maintenance import detect_oscillation
        from app.proactive.settings import get_setting
        actions = ["SILENCE", "LOOKUP"] * 5
        for i, a in enumerate(actions):
            self.db.conn().execute(
                "INSERT INTO decision_log (layer, action, budget, ts) VALUES (2,?,?,?)",
                (a, 0.2 + (0.5 if i % 2 else 0.0), datetime.now(timezone.utc)
                 .astimezone().isoformat(timespec="seconds")))
        self.db.conn().commit()
        self.assertTrue(detect_oscillation(self.db))
        self.assertTrue(float(get_setting("oscillation_damping_until", self.db) or 0) > 0)


if __name__ == "__main__":
    unittest.main()
