"""R0/R1/R16/R17 生命循环底座测试。

覆盖：PRAGMA 固化 / 初始认知边种子 / tick 绝对时间与单事务 / 静默决策模拟 /
R17 校验拒写 / 检查点环形 / 回滚 / Key 探测（mock transport）。
运行：python -m pytest tests/test_life.py -q
"""
import asyncio
import unittest
from pathlib import Path

import httpx

from app.db import Database
from app.llm.client import DeepSeekClient
from app.life.loop import LifeLoop
from app.life.planning import simulate_silent_planning
from app.life.state import (GlobalCognitiveState, checkpoint_id,
                            rollback_to_last_checkpoint, save_checkpoint,
                            validate_domain, write_checked)

TEST_DB = Path(r"D:\DeepSeek Harness\mind-service\.tmp\test_life.db")


class LifeTestCase(unittest.TestCase):
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

    def run_tick(self, loop: LifeLoop, tick: int | None = None):
        return asyncio.run(loop.run_once(tick=tick))


class TestR0Hardening(LifeTestCase):
    def test_pragma_synchronous_normal(self):
        row = self.db.conn().execute("PRAGMA synchronous").fetchone()
        self.assertEqual(row[0], 1)  # 1 = NORMAL

    def test_pragma_wal_autocheckpoint(self):
        row = self.db.conn().execute("PRAGMA wal_autocheckpoint").fetchone()
        self.assertEqual(row[0], 1000)

    def test_seed_edge_exists(self):
        row = self.db.conn().execute(
            "SELECT src, dst, relation, pred_error FROM graph_edges"
            " WHERE src='自我' AND dst='存在'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["pred_error"], 0.3)

    def test_pred_error_column_migrated(self):
        cols = {r["name"] for r in self.db.conn().execute("PRAGMA table_info(graph_edges)")}
        self.assertIn("pred_error", cols)
        cols = {r["name"] for r in self.db.conn().execute("PRAGMA table_info(conversations)")}
        self.assertIn("is_degraded", cols)

    def test_key_probe_auth_fails(self):
        async def handler(request):
            return httpx.Response(401, text="{}")

        client = DeepSeekClient(api_key="bad-key",
                                transport=httpx.MockTransport(handler))
        probe = asyncio.run(client.probe(timeout=5.0))
        self.assertFalse(probe["ok"])
        self.assertEqual(probe["detail"], "auth")

    def test_key_probe_ok(self):
        async def handler(request):
            self.assertEqual(request.url.path, "/models")
            return httpx.Response(200, json={"data": []})

        client = DeepSeekClient(api_key="good-key",
                                transport=httpx.MockTransport(handler))
        probe = asyncio.run(client.probe(timeout=5.0))
        self.assertTrue(probe["ok"])
        self.assertEqual(probe["status"], 200)


class TestR1LifeLoop(LifeTestCase):
    def test_tick_writes_life_log_and_ticks(self):
        loop = LifeLoop(self.db)
        r = self.run_tick(loop, tick=1)
        self.assertTrue(r["ok"])
        rows = self.db.conn().execute(
            "SELECT * FROM life_log WHERE event='tick'").fetchall()
        self.assertGreaterEqual(len(rows), 1)
        self.assertEqual(rows[0]["tick"], 1)
        cap = self.db.conn().execute(
            "SELECT count FROM capability_usage WHERE capability='life_tick'"
        ).fetchone()
        self.assertIsNotNone(cap)

    def test_tick_budget_recharges_by_elapsed(self):
        import time
        loop = LifeLoop(self.db)
        loop._last_tick_ts = time.time() - 60.0  # 预置上次 tick 为 60s 前
        self.db.conn().execute("UPDATE homeostatic_state SET budget=0.5 WHERE id=1")
        self.db.conn().commit()
        self.run_tick(loop, tick=1)
        budget = self.db.conn().execute(
            "SELECT budget FROM homeostatic_state WHERE id=1").fetchone()["budget"]
        self.assertGreater(budget, 0.5)  # +0.01×elapsed/60，elapsed>0 必有回升

    def test_tick_refreshes_singleton_snapshot(self):
        loop = LifeLoop(self.db)
        self.run_tick(loop, tick=1)
        snap = loop.state.snapshot()
        self.assertIn("budget", snap)
        self.assertIn("p_self", snap)
        self.assertIsNotNone(snap["top_pe_edge"])  # 初始边存在

    def test_silent_planning_runs_every_5_ticks(self):
        loop = LifeLoop(self.db)
        self.run_tick(loop, tick=5)
        latent = GlobalCognitiveState(self.db).snapshot()["latent_intentions"]
        self.assertGreaterEqual(len(latent), 1)
        self.assertIn("action", latent[0])
        self.assertIn("G", latent[0])


class TestR16SilentPlanning(LifeTestCase):
    def test_silent_planning_picks_action_and_persists(self):
        result = simulate_silent_planning(self.db, tick=1)
        self.assertIn(result["action"], ("observe_edge", "pending_question", "self_note"))
        self.assertLessEqual(result["G"], result["metabolic"])  # G=−认知+代谢 ≤ 代谢
        latent = GlobalCognitiveState(self.db).snapshot()["latent_intentions"]
        self.assertEqual(latent[-1]["action"], result["action"])

    def test_silent_planning_no_edges_still_runs(self):
        self.db.conn().execute("DELETE FROM graph_edges")
        self.db.conn().commit()
        result = simulate_silent_planning(self.db, tick=2)
        self.assertIsNotNone(result["action"])
        self.assertIsNone(result["edge"])  # 空图不报错、无 latent 锚点边


class TestR17Validation(LifeTestCase):
    def test_write_checked_rejects_out_of_domain(self):
        ok, value = write_checked(self.db, "homeostatic_state", "budget", "budget", 5.0)
        self.assertFalse(ok)
        budget = self.db.conn().execute(
            "SELECT budget FROM homeostatic_state WHERE id=1").fetchone()["budget"]
        self.assertEqual(budget, 0.7)  # 保留上值

    def test_write_checked_accepts_in_domain(self):
        ok, value = write_checked(self.db, "self_model", "p_self", "p_self", 0.9)
        self.assertTrue(ok)
        self.assertAlmostEqual(value, 0.9)

    def test_validate_domain_bounds(self):
        self.assertTrue(validate_domain("budget", 1.0))
        self.assertFalse(validate_domain("budget", 1.01))
        self.assertTrue(validate_domain("p_self", 0.15))
        self.assertFalse(validate_domain("p_self", 0.14))
        self.assertTrue(validate_domain("loneliness", 1.5))
        self.assertFalse(validate_domain("loneliness", 1.6))

    def test_checkpoint_ring_and_rollback(self):
        self.db.conn().execute("UPDATE homeostatic_state SET budget=0.42 WHERE id=1")
        self.db.conn().commit()
        save_checkpoint(self.db, tick=10)
        self.db.conn().execute("UPDATE homeostatic_state SET budget=0.9 WHERE id=1")
        self.db.conn().execute("UPDATE self_model SET p_self=0.2 WHERE id=1")
        self.db.conn().commit()
        self.assertTrue(rollback_to_last_checkpoint(self.db))
        budget = self.db.conn().execute(
            "SELECT budget FROM homeostatic_state WHERE id=1").fetchone()["budget"]
        self.assertAlmostEqual(budget, 0.42, places=5)
        p = self.db.conn().execute(
            "SELECT p_self FROM self_model WHERE id=1").fetchone()["p_self"]
        self.assertAlmostEqual(p, 0.85, places=5)

    def test_checkpoint_id_in_ring_range(self):
        for minutes in (0, 599, 600, 59999):
            cid = checkpoint_id(minutes * 60)
            self.assertGreaterEqual(cid, 1)
            self.assertLessEqual(cid, 100)


if __name__ == "__main__":
    unittest.main()
