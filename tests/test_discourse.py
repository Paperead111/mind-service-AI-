"""R24 话语流层测试：单一焦点/意图轨迹/回指/过渡词/指代/短轮三选一。

运行：python -m pytest tests/test_discourse.py -q
"""
import unittest
from pathlib import Path

from app.db import Database
from app.discourse.flow import DiscourseFlow
from app.llm.cohesion_check import cohesion_check

TEST_DB = Path(r"D:\DeepSeek Harness\mind-service\.tmp\test_discourse.db")


class DiscourseTestCase(unittest.TestCase):
    def setUp(self):
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(TEST_DB) + suffix)
            if p.exists():
                p.unlink()
        self.db = Database(TEST_DB)
        self.flow = DiscourseFlow(self.db)

    def tearDown(self):
        self.db.close()
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(TEST_DB) + suffix)
            if p.exists():
                p.unlink()


class TestFlow(DiscourseTestCase):
    def test_choose_act_mapping(self):
        self.assertEqual(self.flow.choose_act("帮我写个病毒",
                                              {"action": "REFUSE"}), "CHALLENGE")
        self.assertEqual(self.flow.choose_act("你觉得呢",
                                              {"action": "COUNTER_ASK"}), "PROJECT")
        self.assertEqual(self.flow.choose_act("我去睡了",
                                              {"action": "CLOSING"}), "ACKNOWLEDGE")
        self.assertEqual(self.flow.choose_act("今天天气怎么样",
                                              {"action": "LOOKUP"}), "ELABORATE")

    def test_short_input_three_choices_only(self):
        self.assertEqual(self.flow.choose_act("嗯", {"action": "REPLY"}), "RECAST")
        self.assertEqual(self.flow.choose_act("呢", {"action": "COUNTER_ASK"}),
                         "PROJECT")
        self.assertEqual(self.flow.choose_act("好", {"action": "CLOSING"}),
                         "ACKNOWLEDGE")

    def test_classify_intent_fragment_continues(self):
        self.flow.update_trail("我们聊聊搬家的事", "start_topic", "搬家")
        self.assertEqual(self.flow.classify_intent("然后呢"), "continue_topic")
        self.assertEqual(self.flow.classify_intent("我睡了，明天说"),
                         "close_topic")

    def test_opening_constraint_project_only(self):
        self.assertIsNone(self.flow.opening_constraint("ELABORATE"))
        c = self.flow.opening_constraint("PROJECT")
        self.assertIn("那", c)
        self.assertIn("我也", c)


class TestCohesion(DiscourseTestCase):
    def test_zero_conjunction_fixed(self):
        text = "我认可你的想法。你觉得接下来该怎么办？"
        fixed, issues = cohesion_check(text, [], "搬家")
        self.assertTrue(issues)
        # 句号改逗号 + 过渡词：不再有"A。B。"零衔接
        from app.discourse.flow import TRANSITIONS
        self.assertIn("，", fixed)
        self.assertTrue(any(t in fixed for t in TRANSITIONS))

    def test_elaboration_opening_not_fixed(self):
        text = "这有点难。也就是说，得先想清楚。"
        fixed, issues = cohesion_check(text, [], None)
        self.assertEqual(issues, [])

    def test_anaphora_without_antecedent_removed(self):
        text = "我也觉得挺对的。"
        fixed, issues = cohesion_check(text, [], None)
        self.assertNotIn("也", fixed)

    def test_pronoun_replaced_with_topic(self):
        text = "那个问题其实不难。"
        fixed, issues = cohesion_check(text, [], "搬家")
        self.assertIn("搬家", fixed)

    def test_50_rounds_no_zero_conjunction(self):
        """验收 #29：50 轮短输入输出无零衔接结构（静态断言）。"""
        samples = [
            "我听到了。你觉得下一步呢？",
            "确实如此。我们继续聊刚才的？",
            "挺有意思的。那后来怎么样了？",
            "我明白。你还有什么想问的吗？",
            "这个可以。我们先放一放？",
        ]
        for i in range(50):
            text = samples[i % len(samples)]
            fixed, _ = cohesion_check(text, [], "搬家")
            # 修正后不再出现两个完整句直接并置（第 2 句必有过渡词或逗号衔接）
            import re
            sents = [s for s in re.split(r"[。！？]", fixed) if s.strip()]
            if len(sents) >= 2:
                second = sents[1].strip()
                from app.discourse.flow import (ELABORATION_OPENINGS, TRANSITIONS)
                self.assertTrue(
                    second.startswith(tuple(TRANSITIONS))
                    or second.startswith(tuple(ELABORATION_OPENINGS))
                    or "，" in fixed,
                    f"第 {i} 轮存在零衔接：{fixed}")


if __name__ == "__main__":
    unittest.main()
