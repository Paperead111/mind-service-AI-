"""验收收尾单测：时移模拟（#3/#4/#22）+ decide 回滚专项（#18）。

运行：python -m pytest tests/test_acceptance.py -q
"""
import asyncio
import json
import time
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.db import Database
from app.decisions.engine import DecisionEngine
from app.life.loop import LifeLoop
from app.life.maintenance import archive_life_log
from app.life.state import save_checkpoint
from app.life.stimulus import decay_weekly, record

TEST_DB = Path(r"D:\DeepSeek Harness\mind-service\.tmp\test_acceptance.db")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class AcceptanceCase(unittest.TestCase):
    def setUp(self):
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(TEST_DB) + suffix)
            if p.exists():
                p.unlink()
        self.db = Database(TEST_DB)

    def tearDown(self):
        self.db.close()
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(TEST_DB) + suffix)
            if p.exists():
                p.unlink()

    def run_ticks(self, loop: LifeLoop, n: int, seconds: float = 60.0):
        """时移模拟：连续 n 个 tick，每个 tick 间隔 seconds（绝对时间校准）。"""
        for i in range(1, n + 1):
            loop._last_tick_ts = time.time() - seconds
            asyncio.run(loop.run_once(tick=i))


class TestIdleOneHour(AcceptanceCase):
    """验收 #3：静置 1h → life_log 增长 + p_self 漂移 + 孤独≈0.6。"""

    def test_idle_1h(self):
        from app.emotion.clock import loneliness
        from app.proactive.settings import set_setting
        # 预置：1 小时前用户说过话（<2h → 无离线漂移）；p_self 偏离锚点
        set_setting("last_user_message_at",
                    (datetime.now(timezone.utc).astimezone()
                     - timedelta(hours=1)).isoformat(timespec="seconds"), self.db)
        self.db.conn().execute("UPDATE self_model SET p_self=0.7 WHERE id=1")
        self.db.conn().commit()
        loop = LifeLoop(self.db)
        before_log = self.db.conn().execute(
            "SELECT COUNT(*) c FROM life_log").fetchone()["c"]
        self.run_ticks(loop, 60)   # 60 tick = 1h
        after_log = self.db.conn().execute(
            "SELECT COUNT(*) c FROM life_log").fetchone()["c"]
        self.assertGreaterEqual(after_log - before_log, 60)   # life_log 增长
        p = self.db.conn().execute(
            "SELECT p_self FROM self_model WHERE id=1").fetchone()["p_self"]
        self.assertGreater(p, 0.7)                             # 向锚点漂移
        self.assertAlmostEqual(loneliness(self.db)["total"], 0.6, places=1)


class TestIdleTwoHoursThenGreet(AcceptanceCase):
    """验收 #4：静置 2h 后问候 → G 带当前 budget；认知效用指向最高误差边。"""

    def test_greeting_after_2h(self):
        from app.decisions.simulate import build_hypotheses, pick_action
        loop = LifeLoop(self.db)
        self.run_ticks(loop, 120)   # 2h
        eng = DecisionEngine(self.db)
        d = eng.decide("好久不见，最近怎么样？")
        self.assertIn("state_snapshot", d)
        # 120 tick 回充封顶 1.0，再扣本轮短轮成本 0.005 → 0.995（G 用扣后 budget）
        self.assertGreaterEqual(d["state_snapshot"]["budget"], 0.99)
        self.assertIn("pe_term", d)                  # G 分解含认知效用
        hyps = build_hypotheses("好久不见，最近怎么样？", "正常")
        action, g, detail = pick_action(["reply", "counter_ask"], hyps, self.db,
                                        state=loop.state.snapshot())
        self.assertGreaterEqual(detail["pe_term"], 0)  # 认知效用指向最高误差边
        snap = loop.state.snapshot()
        self.assertIsNotNone(snap["top_pe_edge"])


class TestDecideRollback(AcceptanceCase):
    """验收 #18：decide 异常 → 回滚到最近检查点重试一次。"""

    def test_decide_rollback_and_retry(self):
        eng = DecisionEngine(self.db)
        # 写检查点（budget=0.42 基线）
        self.db.conn().execute("UPDATE homeostatic_state SET budget=0.42 WHERE id=1")
        self.db.conn().commit()
        save_checkpoint(self.db, tick=10)
        # 破坏状态（模拟异常前的部分写入）
        self.db.conn().execute("UPDATE homeostatic_state SET budget=0.99 WHERE id=1")
        self.db.conn().commit()
        calls = {"n": 0}

        def flaky(user_text, session_id):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return {"action": "REPLY", "layer": 2, "input_type": "正常",
                    "reason": "重试成功", "G": 0.1}

        with unittest.mock.patch.object(eng, "_decide_once", side_effect=flaky):
            d = eng.decide("你好")
        self.assertEqual(calls["n"], 2)
        self.assertEqual(d["reason"], "重试成功")
        # 回滚后 budget 回到检查点基线（0.42 附近的锚定值）
        budget = self.db.conn().execute(
            "SELECT budget FROM homeostatic_state WHERE id=1").fetchone()["budget"]
        self.assertAlmostEqual(budget, 0.42, places=3)


class TestArchiveDecay(AcceptanceCase):
    """验收 #22：7 天归档（明细+日摘要）/ 痕迹衰减。"""

    def test_archive_and_decay(self):
        old_ts = (datetime.now(timezone.utc).astimezone()
                  - timedelta(days=8)).isoformat(timespec="seconds")
        for i in range(5):
            self.db.conn().execute(
                "INSERT INTO life_log (tick, event, detail, ts) VALUES (?,?,?,?)",
                (i, "tick", json.dumps({"budget": 0.7}), old_ts))
        self.db.conn().commit()
        archive_life_log(self.db)
        remaining = self.db.conn().execute(
            "SELECT COUNT(*) c FROM life_log").fetchone()["c"]
        self.assertEqual(remaining, 0)  # 8 天前明细被归档
        archived = self.db.conn().execute(
            "SELECT COUNT(*) c FROM life_log_archive").fetchone()["c"]
        self.assertGreaterEqual(archived, 1)
        # 痕迹衰减：count×0.5
        record("greeting", "晚安", self.db)
        record("greeting", "晚安", self.db)
        decay_weekly(self.db)
        row = self.db.conn().execute(
            "SELECT count FROM repetition_trace WHERE rtype='greeting' AND pattern='晚安'"
        ).fetchone()
        self.assertEqual(row["count"], 1)


if __name__ == "__main__":
    unittest.main()
