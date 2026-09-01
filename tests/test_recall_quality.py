"""P9 记忆召回质量测试：20 条中文记忆 + 同义改写查询，top-1 命中率 ≥80%。

运行：python -m unittest discover -s tests -v
"""
import unittest
from pathlib import Path

from app.db import Database
from app.memory.recall import recall
from app.memory.store import MemoryStore

TEST_DB = Path(r"D:\DeepSeek Harness\mind-service\.tmp\test_recall.db")

# (记忆内容, 类型, 同义改写查询, 命中标记)
CASES = [
    ("用户最喜欢的歌手是周杰伦", "semantic", "他喜欢哪个歌手", "周杰伦"),
    ("用户讨厌下雨天", "semantic", "下雨的时候他心情怎样", "下雨"),
    ("用户现在住在杭州", "semantic", "他住哪个城市", "杭州"),
    ("用户的生日是三月十五号", "semantic", "他生日什么时候", "三月十五"),
    ("用户养了一只叫团子的猫", "semantic", "他的猫叫什么名字", "团子"),
    ("上周五我们一起看了电影流浪地球", "episodic", "上周五看的电影是什么", "流浪地球"),
    ("用户正在学python编程", "semantic", "他最近在学什么", "python"),
    ("用户最喜欢的颜色是蓝色", "semantic", "他喜欢什么颜色", "蓝色"),
    ("用户每天凌晨两点才睡觉", "semantic", "他几点睡觉", "凌晨两点"),
    ("用户讨厌香菜", "semantic", "他吃不吃香菜", "香菜"),
    ("用户的工作是做游戏开发", "semantic", "他做什么工作", "游戏开发"),
    ("昨天用户说想辞职创业", "episodic", "昨天他说了辞职创业的事吗", "辞职创业"),
    ("用户最想去日本旅行", "semantic", "他梦想去哪里旅行", "日本"),
    ("用户的手机是小米的", "semantic", "他用什么手机", "小米"),
    ("用户喜欢喝冰美式咖啡", "semantic", "他爱喝什么咖啡", "咖啡"),
    ("用户的妹妹在读高中", "semantic", "他妹妹读高中吗", "高中"),
    ("上次他加班到深夜心情很差", "episodic", "上次加班他怎么样", "加班"),
    ("用户老家在四川成都", "semantic", "他老家是哪里的", "成都"),
    ("用户最近在减肥", "semantic", "他最近减肥减得怎么样", "减肥"),
    ("用户觉得真诚比聪明重要", "semantic", "真诚和聪明他更看重哪个", "真诚"),
]


class RecallQualityTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(TEST_DB) + suffix)
            if p.exists():
                p.unlink()
        cls.db = Database(TEST_DB)
        store = MemoryStore(cls.db)
        for content, kind, _, _ in CASES:
            if kind == "semantic":
                store.add_semantic(content, confidence=0.7)
            else:
                store.add_episodic(content, importance=0.7)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(TEST_DB) + suffix)
            if p.exists():
                p.unlink()

    def test_20_queries_recall_rate_at_least_80_percent(self):
        self.assertEqual(len(CASES), 20)
        hits = 0
        misses = []
        for content, kind, query, marker in CASES:
            results = recall(query, k=5, database=self.db)
            top = results[0]["content"] if results else ""
            if marker in top:
                hits += 1
            else:
                misses.append((query, marker, top))
        rate = hits / len(CASES)
        self.assertGreaterEqual(rate, 0.8,
                                f"召回率 {rate:.0%} < 80%，未命中：{misses}")


if __name__ == "__main__":
    unittest.main()
