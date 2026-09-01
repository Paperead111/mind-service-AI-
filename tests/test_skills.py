"""技能系统单元测试：加载/触发匹配/决策选择/脚本执行。

运行：python -m unittest discover -s tests -v
"""
import unittest
from pathlib import Path

from app.db import Database
from app.decisions.engine import DecisionEngine
from app.skills.loader import get_skill, list_skills, match_skill, run_skill

TEST_DB = Path(r"D:\DeepSeek Harness\mind-service\.tmp\test_skills.db")


class SkillsTestCase(unittest.TestCase):
    def setUp(self):
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(TEST_DB) + suffix)
            if p.exists():
                p.unlink()
        self.db = Database(TEST_DB)
        self.engine = DecisionEngine(self.db)

    def tearDown(self):
        self.db.close()
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(TEST_DB) + suffix)
            if p.exists():
                p.unlink()


class TestLoader(SkillsTestCase):
    def test_starter_skills_discovered(self):
        skills = list_skills(reload=True)
        names = {s["dir"] for s in skills}
        self.assertIn("daily-review", names)
        self.assertIn("memory-lookup", names)
        self.assertIn("goal-breakdown", names)
        self.assertIn("writing-polish", names)
        self.assertGreaterEqual(len(skills), 4)

    def test_match_by_trigger(self):
        s = match_skill("帮我拆一下这个目标")
        self.assertIsNotNone(s)
        self.assertEqual(s["dir"], "goal-breakdown")
        self.assertIsNone(match_skill("今天吃什么好呢"))

    def test_skill_fields(self):
        s = get_skill("goal-breakdown")
        self.assertTrue(s["has_script"])
        self.assertIn("使用说明", s["body"])
        self.assertTrue(any(t["name"] == "add_goal" for t in s["tools"]))


class TestEngineSkill(SkillsTestCase):
    def test_decide_selects_skill_action(self):
        d = self.engine.decide("帮我拆一下这个目标")
        self.assertEqual(d["action"], "SKILL")
        self.assertEqual(d["skill_name"], "goal-breakdown")
        self.assertIn("G", d)

    def test_no_skill_on_normal_input(self):
        d = self.engine.decide("今天几号")
        self.assertEqual(d["action"], "REPLY")

    def test_execute_skill_returns_body_and_output(self):
        d = self.engine.decide("帮我拆一下这个目标")
        body, output = self.engine.execute_skill(d, "我想学英语，不知道从哪开始")
        self.assertIn("使用说明", body)
        self.assertIn("已写入目标", output)


class TestSkillScripts(SkillsTestCase):
    def test_goal_breakdown_writes_goals(self):
        s = get_skill("goal-breakdown")
        out = run_skill(s, self.db, "我想学英语。我打算每天背单词。")
        self.assertIn("已写入目标", out)
        rows = self.db.conn().execute("SELECT COUNT(*) c FROM goals").fetchone()
        self.assertGreaterEqual(rows["c"], 1)

    def test_daily_review_returns_records(self):
        self.db.conn().execute(
            "INSERT INTO conversations (role, content, ts)"
            " VALUES ('user','今天聊了聊天气',datetime('now','localtime'))")
        self.db.conn().commit()
        s = get_skill("daily-review")
        out = run_skill(s, self.db, "回顾一下")
        self.assertIn("对方", out)
        self.assertIn("聊了聊天气", out)

    def test_memory_lookup_uses_recall(self):
        from app.memory.store import MemoryStore
        MemoryStore(self.db).add_episodic("用户最喜欢的歌手是周杰伦")
        s = get_skill("memory-lookup")
        out = run_skill(s, self.db, "还记得我喜欢哪个歌手吗")
        self.assertIn("周杰伦", out)


class TestTools(SkillsTestCase):
    def test_tools_declared_in_front_matter(self):
        s = get_skill("memory-lookup")
        self.assertTrue(any(t["name"] == "memory_recall" for t in s["tools"]))

    def test_collect_tools_openai_format(self):
        from app.skills.loader import collect_tools
        tools = collect_tools()
        names = {t["function"]["name"] for t in tools}
        self.assertIn("memory-lookup__memory_recall", names)
        self.assertIn("goal-breakdown__add_goal", names)
        self.assertIn("daily-review__recent_conversations", names)
        self.assertIn("weather__get_weather", names)
        self.assertTrue(all(t["function"]["parameters"] for t in tools))

    def test_dispatch_unknown_tool(self):
        from app.skills.loader import dispatch_tool
        self.assertIn("没有工具", dispatch_tool("nope__nope", {}))

    def test_memory_recall_handler_with_temp_db(self):
        import importlib.util
        from pathlib import Path
        from app.memory.store import MemoryStore
        MemoryStore(self.db).add_semantic("用户最喜欢的歌手是周杰伦")
        spec = importlib.util.spec_from_file_location(
            "skill_memory_lookup",
            Path(r"D:\DeepSeek Harness\mind-service\data\skills\memory-lookup\skill.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        out = mod.memory_recall({"query": "歌手"}, database=self.db)
        self.assertIn("周杰伦", out)

    def test_add_goal_handler_with_temp_db(self):
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "skill_goal",
            Path(r"D:\DeepSeek Harness\mind-service\data\skills\goal-breakdown\skill.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        out = mod.add_goal({"content": "学英语"}, database=self.db)
        self.assertIn("已写入目标", out)
        rows = self.db.conn().execute("SELECT COUNT(*) c FROM goals").fetchone()
        self.assertEqual(rows["c"], 1)


if __name__ == "__main__":
    unittest.main()
