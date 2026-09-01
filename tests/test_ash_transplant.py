"""ash 架构移植（A–E）测试。

运行：python -m pytest tests/test_ash_transplant.py -q
"""
import asyncio
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.db import Database
from app.llm.context_builder import (build_weighted_context, maybe_roll_summary,
                                     recent_turn_block, summary_due, summary_text)
from app.llm.fidelity import (correction_note, judge_fidelity,
                              needs_regeneration, rule_screen)
from app.llm.retrieval import memory_block, memory_need_judgment
from app.life.flowjournal import (latest_unsurfaced, mark_surfaced,
                                  maybe_generate_thought, quota_left, should_think)
from app.persona.layer import PersonaLayer
from app.proactive.settings import set_setting

TEST_DB = Path(r"D:\DeepSeek Harness\mind-service\.tmp\test_ash.db")


class FakeLLM:
    def __init__(self, text="", json_obj=None):
        self.text, self.json_obj, self.calls = text, json_obj, []

    async def chat(self, messages, temperature=0.7, max_tokens=None):
        self.calls.append(("chat", messages))
        return self.text

    async def chat_json(self, messages, temperature=0.3, max_tokens=None):
        self.calls.append(("json", messages))
        return self.json_obj or {"score": 5, "issues": [], "verdict": "keep"}


class Base(unittest.TestCase):
    def setUp(self):
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(TEST_DB) + suffix)
            if p.exists():
                p.unlink()
        self.db = Database(TEST_DB)
        self.persona = PersonaLayer()

    def tearDown(self):
        self.db.close()
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(TEST_DB) + suffix)
            if p.exists():
                p.unlink()


def snap(**kw):
    s = {"budget": 0.7, "p_self": 0.85, "silent_ticks": 0,
         "top_pe_edge": {"pred_error": 0.1}, "loneliness": 0.0,
         "dominant": "平静"}
    s.update(kw)
    return s


class TestA(Base):
    def test_need_judgment_rules(self):
        d = {"context": {"domain": "general", "domain_confidence": None}}
        self.assertEqual(memory_need_judgment("你还记得我们上次聊的那个方案吗", d, snap())[1],
                         "recall_word")
        d2 = {"context": {"domain": "编程", "domain_confidence": "unknown"}}
        self.assertEqual(memory_need_judgment("这个接口怎么调", d2, snap())[1],
                         "unknown_domain")
        self.assertEqual(memory_need_judgment("这接口怎么调", d2, snap())[1],
                         "unknown_domain")
        self.assertEqual(memory_need_judgment("随便聊聊今天天气", d, snap())[0], False)

    def test_memory_block_format(self):
        block = memory_block([{"kind": "episodic", "content": "上周一起修了启动脚本"}])
        self.assertIn("权重0.7", block)
        self.assertIsNone(memory_block([]))


class TestB(Base):
    def test_weighted_tiers_present(self):
        out = build_weighted_context(
            persona_prompt="人格核心", recent_turns=["你：早", "我：早"],
            memory_block_text="[权重0.7·记忆检索] 记忆", extra_ctx=["[目标] x"],
            params_block="[状态参数]", opening=None, skill_hints=[],
            summary_text="较早对话摘要")
        self.assertIn("[权重1.0·人格核心]", out)
        self.assertIn("[权重0.3·会话摘要]", out)
        self.assertIn("[权重0.7·记忆检索]", out)
        self.assertIn("[权重0.9·近期对话]", out)
        self.assertIn("[权重0.7·上下文]", out)

    def test_recent_turn_block_filters(self):
        from app.memory.store import MemoryStore
        st = MemoryStore(self.db)
        st.log_conversation("user", "你好呀，我来了")
        st.log_conversation("assistant", "在的，你说吧")
        st.log_conversation("assistant", "碎", is_degraded=1)
        block = recent_turn_block(self.db, n=4)
        self.assertTrue(any("你好呀" in b for b in block))
        self.assertFalse(any("碎" == b.split("：")[-1] for b in block))

    def test_roll_summary_writes_and_trims(self):
        from app.memory.store import MemoryStore
        st = MemoryStore(self.db)
        for i in range(25):
            st.log_conversation("user", f"第{i}条用户消息，聊了很多内容")
            st.log_conversation("assistant", f"第{i}条回复内容")
        llm = FakeLLM(text="这是滚动摘要内容")
        out = asyncio.run(maybe_roll_summary("新消息", self.db, llm=llm))
        self.assertEqual(out, "这是滚动摘要内容")
        self.assertIn("滚动摘要内容", summary_text(self.db) or "")
        # 再滚 4 次，保留段数 ≤3
        for _ in range(4):
            for i in range(25):
                st.log_conversation("user", f"再来一批{i}")
                st.log_conversation("assistant", f"回复一批{i}")
            asyncio.run(maybe_roll_summary("再来", self.db, llm=llm))
        rows = self.db.conn().execute(
            "SELECT COUNT(*) c FROM conversation_summary").fetchone()["c"]
        self.assertLessEqual(rows, 3)


class TestC(Base):
    def test_rule_screen_hits(self):
        self.assertTrue(rule_screen("很高兴为您服务", self.persona))
        self.assertTrue(rule_screen("这句话" + "很长" * 30, self.persona))
        self.assertFalse(rule_screen("在的，你说。", self.persona))

    def test_judge_and_regen_decision(self):
        llm = FakeLLM(json_obj={"score": 2, "issues": ["太像客服"], "verdict": "rewrite"})
        judge = asyncio.run(judge_fidelity("很高兴为您服务", "你好", self.persona, llm))
        self.assertEqual(judge["score"], 2)
        self.assertTrue(needs_regeneration(judge))
        self.assertIn("人格修正", correction_note(judge))
        keep = FakeLLM(json_obj={"score": 4.5, "issues": [], "verdict": "keep"})
        j2 = asyncio.run(judge_fidelity("在的，你说。", "你好", self.persona, keep))
        self.assertFalse(needs_regeneration(j2))


class TestD(Base):
    def test_should_think_gates(self):
        with unittest.mock.patch("app.life.flowjournal.settings") as s:
            s.flow_journal_enabled = False
            s.flow_journal_daily_limit = 3
            self.assertEqual(should_think(snap(), self.db)[1], "disabled")
        set_setting("thought_date",
                    datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d"), self.db)
        set_setting("thought_count", "3", self.db)
        self.assertEqual(should_think(snap(), self.db)[1], "quota")
        set_setting("thought_count", "0", self.db)
        self.assertEqual(should_think(snap(silent_ticks=12), self.db)[0], True)
        self.assertEqual(should_think(snap(budget=0.2), self.db)[1], "low_budget")

    def test_generate_thought_and_surface(self):
        set_setting("thought_count", "0", self.db)
        llm = FakeLLM(text="要是明天不下雨就好了。")
        out = asyncio.run(maybe_generate_thought(0, self.db, llm=llm))
        self.assertTrue(out["generated"])
        th = latest_unsurfaced(self.db)
        self.assertEqual(th["content"], "要是明天不下雨就好了。")
        mark_surfaced(th["id"], self.db)
        self.assertIsNone(latest_unsurfaced(self.db))
        self.assertEqual(quota_left(self.db), 2)


class TestE(Base):
    def test_scan_and_propose(self):
        from app.memory.store import MemoryStore
        st = MemoryStore(self.db)
        for i in range(3):
            st.log_conversation("user", "你太啰嗦了，说重点")
        from app.identity.persona_proposals import due, maybe_propose
        ok, tag, ev = due(self.db)
        self.assertTrue(ok)
        self.assertEqual(tag, "太啰嗦")
        llm = FakeLLM(json_obj={"rule": "先把结论放第一句", "reason": "用户要重点"})
        out = asyncio.run(maybe_propose(self.db, llm=llm))
        self.assertEqual(out["target"], "太啰嗦")
        rows = self.db.conn().execute(
            "SELECT * FROM personality_proposals WHERE status='pending'").fetchall()
        self.assertEqual(len(rows), 1)

    def test_confirm_and_rollback_yaml(self):
        import app.identity.persona_proposals as pp
        tmp = Path(r"D:\DeepSeek Harness\mind-service\.tmp\test_persona_dir")
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        try:
            (tmp / "default" / "voice").mkdir(parents=True)
            yaml_path = tmp / "default" / "voice" / "base.yaml"
            yaml_path.write_text("tone_rules:\n  - 可以直接，但不能冷漠\n",
                                 encoding="utf-8")
            with unittest.mock.patch.object(pp, "PERSONA_DIR", tmp):
                self.db.conn().execute(
                    "INSERT INTO personality_proposals (target, field, current, proposed,"
                    " reason, evidence, status, created_at) VALUES (?,?,?,?,?,?,'pending',?)",
                    ("太啰嗦", "voice.tone_rules", "（无）", "先把结论放第一句",
                     "测试", "[]",
                     datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")))
                self.db.conn().commit()
                r = pp.confirm(1, self.db)
                self.assertTrue(r["ok"])
                content = yaml_path.read_text(encoding="utf-8")
                self.assertIn("先把结论放第一句", content)
                self.assertTrue(list((tmp / "default" / "voice").glob("base.yaml.bak-*")))
                r2 = pp.rollback(1, self.db)
                self.assertTrue(r2["ok"])
                self.assertNotIn("先把结论放第一句",
                                 yaml_path.read_text(encoding="utf-8"))
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
