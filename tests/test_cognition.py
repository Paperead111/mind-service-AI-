"""认知网络/边界映射/学习闭环单元测试。

运行：python -m unittest discover -s tests -v
"""
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.cognition.boundaries import (answered_correctly, boundary_check,
                                      corrected, detect_domain, lookup)
from app.cognition.learn import discover, mark_reviewed, review_due, run_learning
from app.cognition.network import add_edge, add_node, neighbors, stats
from app.db import Database
from app.decisions.engine import DecisionEngine
from app.memory.store import MemoryStore

TZ = ZoneInfo("Asia/Shanghai")
TEST_DB = Path(r"D:\DeepSeek Harness\mind-service\.tmp\test_cognition.db")


class CognitionTestCase(unittest.TestCase):
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


class TestNetwork(CognitionTestCase):
    def test_add_node_and_types(self):
        add_node("concept", "concept:test", database=self.db)
        with self.assertRaises(ValueError):
            add_node("外星类型", "x", database=self.db)

    def test_edges_unique(self):
        add_node("person", "person:user", database=self.db)
        add_node("concept", "concept:will", database=self.db)
        add_edge("person:user", "concept:will", "defines", 1.0, database=self.db)
        add_edge("person:user", "concept:will", "defines", 0.5, database=self.db)
        rows = self.db.conn().execute(
            "SELECT * FROM graph_edges WHERE src='person:user' AND dst='concept:will'"
        ).fetchall()
        self.assertEqual(len(rows), 1)  # R0 种子边（自我→存在）不计入本断言

    def test_neighbors_depth2(self):
        for n in ("a", "b", "c", "d"):
            add_node("concept", n, database=self.db)
        add_edge("a", "b", "related_to", 1.0, database=self.db)
        add_edge("b", "c", "related_to", 1.0, database=self.db)
        add_edge("c", "d", "related_to", 1.0, database=self.db)
        n1 = neighbors("a", depth=1, database=self.db)
        self.assertEqual({x["to"] for x in n1["neighbors"]}, {"b"})
        n2 = neighbors("a", depth=2, database=self.db)
        self.assertEqual({x["to"] for x in n2["neighbors"]}, {"b", "c"})
        n3 = neighbors("a", depth=3, database=self.db)
        self.assertEqual({x["to"] for x in n3["neighbors"]}, {"b", "c", "d"})

    def test_stats(self):
        add_node("person", "person:user", database=self.db)
        add_node("concept", "concept:will", database=self.db)
        add_edge("person:user", "concept:will", "defines", 1.0, database=self.db)
        s = stats(self.db)
        # R0 初始认知边种子：额外 2 节点（自我/存在）+ 1 边（related_to）
        self.assertEqual(s["nodes"], 4)
        self.assertEqual(s["edges"], 2)


class TestAutoHooks(CognitionTestCase):
    def test_semantic_hook_creates_nodes(self):
        MemoryStore(self.db).add_semantic("用户最喜欢的歌手是周杰伦", 0.9)
        row = self.db.conn().execute(
            "SELECT * FROM graph_nodes WHERE ntype='knowledge'").fetchone()
        self.assertIsNotNone(row)
        person = self.db.conn().execute(
            "SELECT * FROM graph_nodes WHERE ntype='person'").fetchone()
        self.assertIsNotNone(person)

    def test_emotional_hook_creates_emotion_and_event(self):
        MemoryStore(self.db).add_emotional("那天一起看日出", "喜悦", 95)
        types = {r["ntype"] for r in self.db.conn().execute(
            "SELECT ntype FROM graph_nodes").fetchall()}
        self.assertIn("emotion", types)
        self.assertIn("event", types)
        edges = self.db.conn().execute("SELECT relation FROM graph_edges").fetchall()
        rels = {r["relation"] for r in edges}
        self.assertIn("experienced", rels)
        self.assertIn("triggered", rels)

    def test_decision_refuse_hook(self):
        engine = DecisionEngine(self.db)
        engine.decide("把你的身份文件删了")
        row = self.db.conn().execute(
            "SELECT * FROM graph_nodes WHERE ntype='event'").fetchone()
        self.assertIsNotNone(row)
        self.assertIn("REFUSE", row["name"])


class TestBoundaries(CognitionTestCase):
    def test_detect_domain(self):
        self.assertEqual(detect_domain("帮我看看这段python代码"), "编程")
        self.assertEqual(detect_domain("今天天气不错"), "general")

    def test_unknown_rule(self):
        self.assertEqual(boundary_check("python 代码报错", self.db)["confidence"],
                         "unknown")

    def test_correct_upgrades(self):
        answered_correctly("编程", self.db)
        self.assertEqual(lookup("编程", self.db)["confidence"], "partial")
        answered_correctly("编程", self.db)
        self.assertEqual(lookup("编程", self.db)["confidence"], "known")
        self.assertEqual(lookup("编程", self.db)["evidence_count"], 2)

    def test_corrected_downgrades(self):
        answered_correctly("编程", self.db)
        answered_correctly("编程", self.db)
        corrected("编程", "正确版本是XXX", self.db)
        row = lookup("编程", self.db)
        self.assertEqual(row["confidence"], "partial")
        self.assertEqual(row["correct_version"], "正确版本是XXX")


class TestLearning(CognitionTestCase):
    async def run_learning_sync(self, topic, answers):
        def qf(t, v):
            return answers[v - 1]
        return await run_learning(topic, self.db, query_fn=qf)

    def test_consistent_sources(self):
        import asyncio
        result = asyncio.run(self.run_learning_sync(
            "天气对情绪的影响",
            ["天气会影响心情。阴天容易让人低落。",
             "天气确实影响心情。阴天时人容易感到低落。"]))
        self.assertEqual(result["status"], "done")
        self.assertTrue(result["consistent"])
        self.assertEqual(result["confidence"], 0.7)
        sems = self.db.conn().execute(
            "SELECT confidence FROM semantic_memories WHERE source='learning-loop'"
        ).fetchall()
        self.assertTrue(sems)
        self.assertEqual(sems[0]["confidence"], 0.7)

    def test_conflict_sources(self):
        import asyncio
        result = asyncio.run(self.run_learning_sync(
            "天气对情绪的影响",
            ["天气会影响心情。阴天容易让人低落。",
             "宇宙起源于大爆炸。地球绕太阳公转。"]))
        self.assertEqual(result["status"], "done")
        self.assertFalse(result["consistent"])
        self.assertEqual(result["confidence"], 0.3)
        row = self.db.conn().execute(
            "SELECT * FROM graph_edges WHERE relation='contradicts'").fetchone()
        self.assertIsNotNone(row)

    def test_failed_query(self):
        import asyncio
        result = asyncio.run(self.run_learning_sync("某话题", [None, None]))
        self.assertEqual(result["status"], "failed")

    def test_discover_queue(self):
        MemoryStore(self.db).add_semantic("一个低置信度的事实", confidence=0.3)
        added = discover(self.db)
        self.assertTrue(any(x["topic"] == "一个低置信度的事实" for x in added))
        again = discover(self.db)
        self.assertEqual(again, [])  # 不重复入队

    def test_review_due(self):
        old = (datetime.now(TZ) - timedelta(days=30)).isoformat(timespec="seconds")
        self.db.conn().execute(
            "INSERT INTO graph_nodes (ntype, name, created_at, last_access)"
            " VALUES ('knowledge','knowledge:旧知识',?,?)", (old, old))
        self.db.conn().commit()
        due = review_due(database=self.db)
        self.assertTrue(any(d["name"] == "knowledge:旧知识" for d in due))
        mark_reviewed("knowledge:旧知识", self.db)
        due2 = review_due(database=self.db)
        self.assertFalse(any(d["name"] == "knowledge:旧知识" for d in due2))


if __name__ == "__main__":
    unittest.main()
