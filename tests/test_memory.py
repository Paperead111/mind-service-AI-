"""记忆子系统单元测试：写入/检索/闪光灯/工作记忆 LRU/永不删除/周压缩。

运行：python -m unittest discover -s tests -v
"""
import unittest
from pathlib import Path

from app.db import Database
from app.memory.compact import weekly_compaction
from app.memory.recall import jaccard, recall, recall_flashbulb
from app.memory.store import MemoryStore

TEST_DB = Path(r"D:\DeepSeek Harness\mind-service\.tmp\test_memory.db")


class MemoryTestCase(unittest.TestCase):
    def setUp(self):
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(TEST_DB) + suffix)
            if p.exists():
                p.unlink()
        self.db = Database(TEST_DB)
        self.store = MemoryStore(self.db)

    def tearDown(self):
        self.db.close()
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(TEST_DB) + suffix)
            if p.exists():
                p.unlink()


class TestStore(MemoryTestCase):
    def test_schema_initialized(self):
        tables = {r[0] for r in self.db.conn().execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("conversations", "episodic_memories", "semantic_memories",
                  "emotional_memories", "working_memory", "memory_index"):
            self.assertIn(t, tables)

    def test_episodic_write_read(self):
        mem_id = self.store.add_episodic("我讨厌下雨天", tags=["天气"], importance=0.8)
        row = self.db.conn().execute(
            "SELECT * FROM episodic_memories WHERE id=?", (mem_id,)).fetchone()
        self.assertEqual(row["content"], "我讨厌下雨天")
        self.assertEqual(row["importance"], 0.8)

    def test_flashbulb_threshold(self):
        lo = self.store.add_emotional("普通小事", "喜悦", 50)
        hi = self.store.add_emotional("重要时刻", "喜悦", 92)
        rows = self.db.conn().execute(
            "SELECT id, weight FROM emotional_memories ORDER BY id").fetchall()
        weights = {r["id"]: r["weight"] for r in rows}
        self.assertEqual(weights[lo], 1.0)
        self.assertEqual(weights[hi], 2.0)  # >80 → 闪光灯

    def test_never_delete_archive_only(self):
        mem_id = self.store.add_episodic("要忘掉的事")
        self.store.archive(mem_id)
        row = self.db.conn().execute(
            "SELECT archived FROM episodic_memories WHERE id=?", (mem_id,)).fetchone()
        self.assertIsNotNone(row)          # 物理存在
        self.assertEqual(row["archived"], 1)  # 只打标记
        # 检索时不可见
        results = recall("要忘掉的事", k=5, database=self.db)
        self.assertFalse(any(r["id"] == mem_id for r in results))


class TestWorkingMemory(MemoryTestCase):
    def test_four_slots_and_lru_eviction(self):
        self.store.set_working("entity", "话题A", "低")
        self.store.set_working("constraint", "约束B", "高")
        self.store.set_working("goal", "目标C", "高")
        self.store.set_working("context", "上下文D", "低")
        # 第 5 条：满员 → 淘汰最低优先级+最久未用（话题A）
        slot = self.store.set_working("entity", "话题E", "中")
        rows = self.db.conn().execute(
            "SELECT wm_type, content, priority FROM working_memory ORDER BY slot").fetchall()
        contents = {r["content"] for r in rows}
        self.assertEqual(len(rows), 4)
        self.assertIn("话题E", contents)
        self.assertNotIn("话题A", contents)   # 低优先级被淘汰
        self.assertIn("约束B", contents)       # 高优先级保留

    def test_clear_working(self):
        self.store.set_working("entity", "临时")
        self.store.clear_working()
        rows = self.db.conn().execute("SELECT * FROM working_memory").fetchall()
        self.assertEqual(len(rows), 0)


class TestRecall(MemoryTestCase):
    def test_related_memory_ranks_first(self):
        self.store.add_episodic("我讨厌下雨天，一下雨就心情不好", importance=0.6)
        self.store.add_episodic("今天去买了菜", importance=0.6)
        results = recall("下雨", k=5, database=self.db)
        self.assertTrue(results)
        self.assertIn("下雨", results[0]["content"])
        self.assertEqual(results[0]["kind"], "episodic")

    def test_semantic_recall(self):
        self.store.add_semantic("用户最喜欢的歌手是周杰伦", confidence=0.9, source="对话")
        results = recall("喜欢什么歌手", k=3, database=self.db)
        self.assertEqual(results[0]["kind"], "semantic")

    def test_flashbulb_recalled_first(self):
        self.store.add_emotional("那天一起看日出", "喜悦", 60)
        self.store.add_emotional("他说的那句承诺", "喜悦", 95)
        rows = recall_flashbulb("喜悦", k=2, database=self.db)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["weight"], 2.0)

    def test_jaccard_basic(self):
        self.assertGreater(jaccard("下雨天心情不好", "下雨天心情很差"), 0.3)
        self.assertEqual(jaccard("甲乙", "丙丁"), 0.0)


class TestCompaction(MemoryTestCase):
    def test_promotes_frequent_topics(self):
        for _ in range(4):
            self.store.log_conversation("user", "聊聊灵魂的事", topic="灵魂")
        self.store.log_conversation("user", "今天天气不错", topic="天气")
        result = weekly_compaction(days=7, min_freq=3, database=self.db)
        topics = {p["topic"]: p["count"] for p in result["promoted"]}
        self.assertIn("灵魂", topics)
        self.assertEqual(topics["灵魂"], 4)
        self.assertNotIn("天气", topics)

    def test_compaction_uses_tags_too(self):
        for i in range(3):
            self.store.add_episodic(f"事件{i}", tags=["信念锚点"])
        result = weekly_compaction(days=7, min_freq=3, database=self.db)
        self.assertTrue(any(p["topic"] == "信念锚点" for p in result["promoted"]))


if __name__ == "__main__":
    unittest.main()
