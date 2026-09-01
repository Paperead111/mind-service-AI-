"""主动对话子系统单元测试：触发器/9步决策链/影子模式/预算/静默/幽灵/反馈调参。

运行：python -m unittest discover -s tests -v
"""
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.db import Database
from app.memory.store import MemoryStore
from app.proactive.engine import HeartbeatEngine, apply_feedback
from app.proactive.settings import get_setting, set_setting
from app.proactive.triggers import evaluate_triggers

TZ = ZoneInfo("Asia/Shanghai")
TEST_DB = Path(r"D:\DeepSeek Harness\mind-service\.tmp\test_proactive.db")
NOW = datetime(2026, 8, 27, 14, 0, tzinfo=TZ)  # 白天 14:00


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


async def ok_compose(messages) -> str:
    return "记得你上次说周五要交作业，交了吗？"


async def fail_compose(messages) -> str:
    raise RuntimeError("模拟 LLM 失败")


class ProactiveTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(TEST_DB) + suffix)
            if p.exists():
                p.unlink()
        self.db = Database(TEST_DB)
        self.engine = HeartbeatEngine(self.db)

    def tearDown(self):
        self.db.close()
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(TEST_DB) + suffix)
            if p.exists():
                p.unlink()

    def seed_user(self, offline_hours: float = 30.0,
                  emotions: list[tuple] | None = None):
        last = NOW - timedelta(hours=offline_hours)
        set_setting("last_user_message_at", iso(last), self.db)
        t = last
        for _ in range(3):
            t = t - timedelta(minutes=10)
            self.db.conn().execute(
                "INSERT INTO conversations (role, content, ts) VALUES ('user','随便聊聊',?)",
                (iso(t),))
        for e, i in (emotions or []):
            t = t - timedelta(minutes=10)
            self.db.conn().execute(
                "INSERT INTO conversations (role, content, emotion, intensity, ts)"
                " VALUES ('user','情绪事件',?,?,?)", (e, i, iso(t)))
        self.db.conn().commit()

    def seed_source(self):
        MemoryStore(self.db).add_episodic("随便聊聊，我上次说要交作业")


class TestSeed(unittest.TestCase):
    def test_triggers_seeded(self):
        dbx = Database(TEST_DB)
        try:
            rows = dbx.conn().execute("SELECT * FROM triggers").fetchall()
            self.assertEqual(len(rows), 11)
            confs = {r["type"]: r["conf"] for r in rows}
            self.assertEqual(confs["return-over"], 0.9)
            self.assertEqual(confs["time-day"], 0.3)
        finally:
            dbx.close()
            for suffix in ("", "-wal", "-shm"):
                p = Path(str(TEST_DB) + suffix)
                if p.exists():
                    p.unlink()


class TestTriggers(ProactiveTestCase):
    async def test_candidates(self):
        self.seed_user(offline_hours=30, emotions=[("难过", 80), ("焦虑", 70), ("平静", 30)])
        candidates = evaluate_triggers(NOW, self.db)
        types = {c["type"] for c in candidates}
        self.assertIn("return-over", types)
        self.assertIn("silence", types)
        self.assertIn("time-day", types)
        self.assertIn("emotion-shift", types)
        ret = next(c for c in candidates if c["type"] == "return-over")
        self.assertEqual(ret["conf"], 0.9)
        self.assertEqual(ret["priority"], "高")

    async def test_cold_start_no_history(self):
        candidates = evaluate_triggers(NOW, self.db)
        types = {c["type"] for c in candidates}
        self.assertIn("time-day", types)       # 时间类冷启动可用
        self.assertNotIn("silence", types)      # 沉默类需历史
        self.assertNotIn("return-over", types)  # 回归类需时间戳


class TestChain(ProactiveTestCase):
    async def test_shadow_mode_records_not_sends(self):
        self.seed_user(); self.seed_source()
        summary = await self.engine.run(now=NOW, compose=ok_compose)
        self.assertGreaterEqual(summary["shadowed"], 1)
        self.assertEqual(summary["sent"], 0)
        rows = self.db.conn().execute("SELECT * FROM shadow_log").fetchall()
        self.assertTrue(rows)
        self.assertIn("交作业", rows[0]["planned_message"])
        sent = self.db.conn().execute("SELECT COUNT(*) c FROM proactive_sent").fetchone()
        self.assertEqual(sent["c"], 0)

    async def test_no_source_dropped(self):
        self.seed_user()  # 有触发器，但记忆库无来源
        summary = await self.engine.run(now=NOW, compose=ok_compose)
        self.assertGreaterEqual(summary["dropped_no_source"], 1)
        self.assertEqual(summary["shadowed"], 0)

    async def test_budget_cap(self):
        self.seed_user(); self.seed_source()
        set_setting("shadow_mode", "false", self.db)
        for i in range(20):
            self.db.conn().execute(
                "INSERT INTO proactive_sent (idempotency_key, trigger_type, message, ts)"
                " VALUES (?,?,?,?)",
                (f"fill:{i}", "time-day", "占位", iso(NOW)))
        self.db.conn().commit()
        summary = await self.engine.run(now=NOW, compose=ok_compose)
        self.assertEqual(summary["sent"], 0)
        self.assertEqual(summary["shadowed"], 0)

    async def test_quiet_hours_defer(self):
        self.seed_user(offline_hours=0.5); self.seed_source()
        night = datetime(2026, 8, 27, 23, 30, tzinfo=TZ)
        summary = await self.engine.run(now=night, compose=ok_compose)
        self.assertGreaterEqual(summary["deferred"], 1)
        self.assertEqual(summary["shadowed"], 0)
        rows = self.db.conn().execute(
            "SELECT COUNT(*) c FROM proactive_deferred WHERE resolved=0").fetchone()
        self.assertEqual(rows["c"], summary["deferred"])

    async def test_ghost_offline_single_message(self):
        self.seed_user(offline_hours=30); self.seed_source()
        set_setting("shadow_mode", "false", self.db)
        summary = await self.engine.run(now=NOW, compose=ok_compose)
        self.assertEqual(summary["sent"], 1)  # 离线>24h → 本次最多 1 条

    async def test_llm_failure_no_quota(self):
        self.seed_user(); self.seed_source()
        set_setting("shadow_mode", "false", self.db)
        summary = await self.engine.run(now=NOW, compose=fail_compose)
        self.assertEqual(summary["sent"], 0)
        sent = self.db.conn().execute("SELECT COUNT(*) c FROM proactive_sent").fetchone()
        self.assertEqual(sent["c"], 0)  # 失败不扣配额

    async def test_empty_greeting_dropped(self):
        self.seed_user(); self.seed_source()

        async def greet(messages):
            return "在吗"

        summary = await self.engine.run(now=NOW, compose=greet)
        self.assertEqual(summary["shadowed"], 0)

    async def test_user_in_conversation_skip(self):
        set_setting("last_user_message_at",
                    iso(NOW - timedelta(seconds=60)), self.db)
        summary = await self.engine.run(now=NOW, compose=ok_compose)
        self.assertIn("正在对话", summary["skipped"])

    async def test_disabled_skip(self):
        set_setting("proactive_enabled", "false", self.db)
        summary = await self.engine.run(now=NOW, compose=ok_compose)
        self.assertIn("关闭", summary["skipped"])

    async def test_cooldown_after_fire(self):
        self.engine._mark_fired("time-night", NOW)
        self.assertTrue(self.engine._in_cooldown("time-night",
                                                 NOW + timedelta(minutes=30)))
        self.assertFalse(self.engine._in_cooldown("time-night",
                                                  NOW + timedelta(minutes=61)))


class TestFeedback(ProactiveTestCase):
    def test_two_negatives_cooldown_and_clamp(self):
        apply_feedback("time-day", "negative", self.db)
        r = apply_feedback("time-day", "negative", self.db)
        self.assertLessEqual(r["conf"], 0.1)   # 0.3−0.25 → 钳制下限 0.1
        row = self.db.conn().execute(
            "SELECT cooldown_until, negative_streak FROM triggers WHERE type='time-day'"
        ).fetchone()
        self.assertEqual(row["negative_streak"], 2)
        self.assertIsNotNone(row["cooldown_until"])  # 连 2 负 → 30 分钟冷却

    def test_three_positives_streak(self):
        for _ in range(3):
            r = apply_feedback("time-night", "positive", self.db)
        self.assertAlmostEqual(r["conf"], 0.95, places=2)  # 0.8+0.15+0.05 → 钳制 0.95
        row = self.db.conn().execute(
            "SELECT positive_streak FROM triggers WHERE type='time-night'").fetchone()
        self.assertEqual(row["positive_streak"], 3)

    def test_positive_resets_negative_streak(self):
        apply_feedback("time-day", "negative", self.db)
        r = apply_feedback("time-day", "positive", self.db)
        row = self.db.conn().execute(
            "SELECT negative_streak, positive_streak FROM triggers WHERE type='time-day'"
        ).fetchone()
        self.assertEqual(row["negative_streak"], 0)
        self.assertEqual(row["positive_streak"], 1)


if __name__ == "__main__":
    unittest.main()
