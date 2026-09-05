"""情绪分离测试：感知用户情绪 vs 自身情绪状态（用户规格三条验收）。

运行：python -m pytest tests/test_emotion_separation.py -q
"""
import unittest
from pathlib import Path

from app.db import Database
from app.decisions.engine import DecisionEngine
from app.emotion.state import EmotionSystem, expression_style

TEST_DB = Path(r"D:\DeepSeek Harness\mind-service\.tmp\test_emotion_sep.db")


class SepCase(unittest.TestCase):
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


class TestSeparation(SepCase):
    def test_perceived_and_self_are_distinct(self):
        """用户表达喜悦：感知层明显积极，自身只轻微偏正（不变成用户的情绪）。"""
        r = self.emotion.perceive("我太开心了哈哈！")
        s = self.emotion.state()
        self.assertGreater(s["user_perceived_valence"], 0.5)   # 感知：用户很开心
        self.assertLess(s["valence"], 0.35)                   # 自身：只是轻微上扬
        self.assertLess(abs(s["valence"]), abs(s["user_perceived_valence"]))
        self.assertEqual(r["emotion"], "joy")

    def test_user_emotion_does_not_overwrite_self(self):
        """用户情绪只写入感知字段，valance/arousal 始终是自身动力学的结果。"""
        self.db.conn().execute(
            "UPDATE emotion_state SET valence=0.5, arousal=0.4 WHERE id=1")
        self.db.conn().commit()
        self.emotion.perceive("我难过死了")
        s = self.emotion.state()
        # 自身从 0.5 只小幅移动（动力学限幅 ≤0.12），绝不会跳到用户的 −0.6
        self.assertGreater(s["valence"], 0.38)
        self.assertLess(s["user_perceived_valence"], -0.3)


class TestDynamics(SepCase):
    def test_five_rounds_sadness_self_stays_above_minus_0_3(self):
        """验收 1：连续悲伤 5 轮，self_valence 不跌破 −0.3，风格为关切支持。"""
        for text in ("我好难过", "真的很伤心", "想哭", "太难过了", "心都碎了"):
            self.emotion.perceive(text)
        s = self.emotion.state()
        self.assertGreaterEqual(s["valence"], -0.3)
        self.assertLess(s["user_perceived_valence"], -0.4)
        style = expression_style(s["user_perceived_valence"], 70,
                                 s["valence"], s["arousal"])
        self.assertEqual(style, "concerned_support")

    def test_anger_alert_not_high_arousal(self):
        """验收 2：愤怒刺激 → 自身 valence 轻微下降、arousal 略升但不高，不误删候选。"""
        self.emotion.perceive("我气死了！他太过分了！")
        s = self.emotion.state()
        self.assertGreater(s["valence"], -0.15)     # 轻微下降
        self.assertGreater(s["arousal"], 0.4)       # 略升（警觉）
        self.assertLess(s["arousal"], 0.8)          # 不进入高唤醒
        # 门控层：arousal 自身 <0.8 → lookup 候选不被剪
        eng = DecisionEngine(self.db)
        cands = ["reply", "counter_ask", "lookup"]
        snap = {"valence": s["valence"], "arousal": s["arousal"],
                "fear": s["fear"], "goals": []}
        removed = eng._gate_candidates(cands, snap)
        self.assertEqual(removed, [])

    def test_low_budget_weakens_following(self):
        """验收 3：budget 极低时对外部情绪的跟随幅度显著减小。"""
        def delta_after_one(budget):
            self.db.conn().execute(
                "UPDATE homeostatic_state SET budget=? WHERE id=1", (budget,))
            self.db.conn().execute(
                "UPDATE emotion_state SET valence=0, user_perceived_valence=0 WHERE id=1")
            self.db.conn().commit()
            self.emotion.perceive("我特别难过")
            return self.emotion.state()["valence"]
        d_high = abs(delta_after_one(0.9))
        d_low = abs(delta_after_one(0.2))
        self.assertGreater(d_high, d_low)
        self.assertLess(d_low, d_high * 0.6)   # 显著减小


class TestStyleEnum(SepCase):
    def test_style_mapping(self):
        self.assertEqual(expression_style(-0.6, 70, 0.1, 0.4), "concerned_support")
        self.assertEqual(expression_style(-0.5, 70, -0.4, 0.5), "empathic")
        self.assertEqual(expression_style(0.0, 30, 0.0, 0.9), "alert")
        self.assertEqual(expression_style(0.0, 30, -0.5, 0.4), "subdued")
        self.assertEqual(expression_style(0.0, 30, 0.1, 0.4), "neutral_support")


if __name__ == "__main__":
    unittest.main()
