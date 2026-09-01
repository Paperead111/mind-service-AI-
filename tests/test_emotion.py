"""情绪系统与内部时钟单元测试。

运行：python -m unittest discover -s tests -v
"""
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.db import Database
from app.decisions.simulate import est_risk
from app.emotion.clock import (accumulate_offline, deep_rounds_recent,
                               goal_anxiety, loneliness)
from app.emotion.state import EmotionSystem, detect
from app.proactive.settings import set_setting

TZ = ZoneInfo("Asia/Shanghai")
TEST_DB = Path(r"D:\DeepSeek Harness\mind-service\.tmp\test_emotion.db")


class EmotionTestCase(unittest.TestCase):
    def setUp(self):
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(TEST_DB) + suffix)
            if p.exists():
                p.unlink()
        self.db = Database(TEST_DB)
        self.emotion = EmotionSystem(self.db)

    def tearDown(self):
        self.db.close()
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(TEST_DB) + suffix)
            if p.exists():
                p.unlink()

    def log_user(self, content, emotion=None, intensity=None):
        self.db.conn().execute(
            "INSERT INTO conversations (role, content, emotion, intensity, ts)"
            " VALUES ('user',?,?,?,datetime('now','localtime'))",
            (content, emotion, intensity))
        self.db.conn().commit()


class TestDetect(EmotionTestCase):
    def test_joy_with_intensifier(self):
        d = detect("我特别开心！")
        self.assertEqual(d["emotion"], "joy")
        self.assertGreaterEqual(d["intensity"], 65)

    def test_fear(self):
        d = detect("我很害怕，特别担心")
        self.assertEqual(d["emotion"], "fear")
        self.assertGreater(d["valence"], -1.0)
        self.assertLess(d["valence"], 0)

    def test_neutral(self):
        d = detect("今天几号")
        self.assertIsNone(d["emotion"])


class TestState(EmotionTestCase):
    def test_perceive_updates_state(self):
        result = self.emotion.perceive("我特别害怕")
        s = self.emotion.state()
        self.assertGreater(s["fear"], 0.2)
        self.assertLess(s["valence"], 0)
        self.assertEqual(result["emotion"], "fear")

    def test_decay_reduces(self):
        self.emotion.perceive("我特别害怕！")
        before = self.emotion.state()["fear"]
        self.emotion.decay(rounds=3)
        after = self.emotion.state()["fear"]
        self.assertLess(after, before)

    def test_modulation_fear_vs_trust(self):
        for _ in range(5):
            self.emotion.perceive("我特别特别害怕！")
        mod = self.emotion.modulation()
        self.assertGreater(mod["risk_multiplier"], 1.0)
        self.assertTrue(mod["meta_freeze"])  # 恐惧>0.8 → 元认知冻结
        # 信任高 → 注意力扩容、风险宽松
        db2_emotion = None
        self.db.conn().execute(
            "UPDATE emotion_state SET fear=0, trust=0.8 WHERE id=1")
        self.db.conn().commit()
        mod2 = self.emotion.modulation()
        self.assertGreaterEqual(mod2["foa_capacity"], 5.0)
        self.assertLess(mod2["risk_multiplier"], 1.0)

    def test_soothe_after_three_negative(self):
        for msg in ("我很难过", "好害怕", "太生气了"):
            self.log_user(msg, emotion=detect(msg)["emotion_cn"],
                          intensity=detect(msg)["intensity"])
        result = self.emotion.perceive("我还是好害怕")
        self.assertIsNotNone(result["soothe"])

    def test_flashbulb_on_high_intensity(self):
        self.emotion.perceive("我特别特别特别开心！")
        rows = self.db.conn().execute(
            "SELECT * FROM emotional_memories").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["weight"], 2.0)


class TestClock(EmotionTestCase):
    def test_loneliness_empty_is_point_six(self):
        # v4.1 公式：孤独 = 0.6×会话距离(空库=1.0) + 离线漂移(0) = 0.6
        self.assertEqual(loneliness(self.db)["total"], 0.6)

    def test_loneliness_drops_with_deep_dialog(self):
        for _ in range(5):
            self.log_user("今天聊了很多很多很多很多很多很多很多很多很多很多内容",
                          intensity=70)
        l = loneliness(self.db)
        self.assertLess(l["total"], 1.0)
        self.assertGreater(l["deep_rounds_recent"], 0)

    def test_accumulate_offline(self):
        set_setting("last_user_message_at",
                    (datetime.now(TZ) - timedelta(hours=5)).isoformat(timespec="seconds"),
                    self.db)
        r = accumulate_offline(self.db)
        self.assertAlmostEqual(r["accumulated"], 0.06, places=2)

    def test_goal_anxiety(self):
        self.db.conn().execute(
            "INSERT INTO goals (content, priority, progress, max_tolerable_idle_hours,"
            " last_progress_at, status) VALUES ('高优先目标', 6, 0.9, 4, ?, 'active')",
            ((datetime.now(TZ) - timedelta(hours=8)).isoformat(timespec="seconds"),))
        self.db.conn().execute(
            "INSERT INTO goals (content, priority, progress, max_tolerable_idle_hours,"
            " last_progress_at, status) VALUES ('刚更新的目标', 3, 0.5, 24, ?, 'active')",
            (datetime.now(TZ).isoformat(timespec="seconds"),))
        self.db.conn().commit()
        out = goal_anxiety(self.db)
        by_name = {o["goal"]: o for o in out}
        self.assertEqual(by_name["高优先目标"]["urgency"], 1.0)
        self.assertAlmostEqual(by_name["高优先目标"]["anxiety"], 0.4, places=2)
        self.assertLess(by_name["刚更新的目标"]["urgency"], 0.1)


class TestEmotionInDecision(EmotionTestCase):
    def test_est_risk_rises_with_negative_valence(self):
        self.emotion.state()  # 播种情绪状态行
        base = est_risk("contest", self.db)
        self.db.conn().execute("UPDATE emotion_state SET valence=-0.5 WHERE id=1")
        self.db.conn().commit()
        risky = est_risk("contest", self.db)
        self.assertGreater(risky, base)  # 情绪影响决策（可观测）


if __name__ == "__main__":
    unittest.main()
