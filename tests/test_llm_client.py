"""LLM 客户端单元测试：用 httpx.MockTransport 模拟 DeepSeek 服务端，不依赖真实 Key。

运行：python -m unittest discover -s tests -v
"""
import json
import unittest

import httpx

from app.llm.client import DeepSeekClient, LLMError


def ok_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"role": "assistant", "content": content}}]},
    )


def make_client(handler, **kw) -> DeepSeekClient:
    """构造带 MockTransport 的客户端（限流放宽、重试次数可控）。"""
    kw.setdefault("api_key", "test-key")
    kw.setdefault("base_url", "http://mock")
    kw.setdefault("max_retries", 2)
    kw.setdefault("rate_limit_per_minute", 1000)
    transport = httpx.MockTransport(handler)
    return DeepSeekClient(transport=transport, **kw)


class TestChat(unittest.IsolatedAsyncioTestCase):
    async def test_success(self):
        client = make_client(lambda req: ok_response("你好"))
        reply = await client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(reply, "你好")

    async def test_request_payload(self):
        seen = {}

        def handler(req: httpx.Request) -> httpx.Response:
            seen["url"] = str(req.url)
            seen["body"] = json.loads(req.content)
            seen["auth"] = req.headers.get("Authorization")
            return ok_response("ok")

        client = make_client(handler)
        await client.chat([{"role": "user", "content": "hi"}], json_mode=True)
        self.assertEqual(seen["url"], "http://mock/chat/completions")
        self.assertEqual(seen["auth"], "Bearer test-key")
        self.assertEqual(seen["body"]["model"], "deepseek-v4-flash")
        self.assertEqual(seen["body"]["response_format"], {"type": "json_object"})

    async def test_retry_on_429_then_success(self):
        calls = {"n": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            return ok_response("第二次成功")

        client = make_client(handler, max_retries=2)
        reply = await client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(reply, "第二次成功")
        self.assertEqual(calls["n"], 2)

    async def test_retry_exhausted_raises(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        client = make_client(handler, max_retries=2)
        with self.assertRaises(LLMError):
            await client.chat([{"role": "user", "content": "hi"}])

    async def test_no_key_raises_without_network(self):
        client = make_client(lambda req: self.fail("不应发请求"), api_key="")
        with self.assertRaises(LLMError) as ctx:
            await client.chat([{"role": "user", "content": "hi"}])
        self.assertIn("DEEPSEEK_API_KEY", str(ctx.exception))


class TestChatJson(unittest.IsolatedAsyncioTestCase):
    async def test_plain_json(self):
        client = make_client(lambda req: ok_response('{"decision": "allow"}'))
        data = await client.chat_json([{"role": "user", "content": "ok?"}])
        self.assertEqual(data, {"decision": "allow"})

    async def test_markdown_fence_tolerated(self):
        content = '```json\n{"a": 1}\n```'
        client = make_client(lambda req: ok_response(content))
        data = await client.chat_json([{"role": "user", "content": "x"}])
        self.assertEqual(data, {"a": 1})

    async def test_extra_text_tolerated_by_brace_extraction(self):
        content = '好的，结果如下：{"a": 1}，完毕。'
        client = make_client(lambda req: ok_response(content))
        data = await client.chat_json([{"role": "user", "content": "x"}])
        self.assertEqual(data, {"a": 1})

    async def test_invalid_then_corrected_retry(self):
        calls = {"n": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            body = json.loads(req.content)
            if calls["n"] == 1:
                return ok_response("这不是 JSON")
            # 第二次调用应带纠正指令
            self.assertIn("不是合法 JSON", body["messages"][-1]["content"])
            return ok_response('{"ok": true}')

        client = make_client(handler, max_retries=2)
        data = await client.chat_json([{"role": "user", "content": "x"}])
        self.assertEqual(data, {"ok": True})
        self.assertEqual(calls["n"], 2)

    async def test_double_failure_raises(self):
        client = make_client(lambda req: ok_response("还是不是 JSON"))
        with self.assertRaises(LLMError):
            await client.chat_json([{"role": "user", "content": "x"}])


class TestRateLimiter(unittest.IsolatedAsyncioTestCase):
    async def test_window_fills_up(self):
        from app.llm.client import MinuteRateLimiter

        limiter = MinuteRateLimiter(max_calls=3)
        await limiter.acquire()
        await limiter.acquire()
        await limiter.acquire()
        self.assertEqual(len(limiter._calls), 3)


class TestChatWithTools(unittest.IsolatedAsyncioTestCase):
    async def test_tool_call_loop(self):
        calls = {"round": 0, "executed": []}

        def handler(req: httpx.Request) -> httpx.Response:
            calls["round"] += 1
            body = json.loads(req.content)
            if calls["round"] == 1:
                self.assertIn("tools", body)
                return httpx.Response(200, json={
                    "choices": [{"message": {
                        "role": "assistant", "content": None,
                        "tool_calls": [{
                            "id": "call_1", "type": "function",
                            "function": {"name": "memory_recall",
                                         "arguments": '{"query": "歌手"}'}}]},
                        "finish_reason": "tool_calls"}]})
            return ok_response("查到了：周杰伦")

        client = make_client(handler, max_retries=1)

        async def execute(name, args):
            calls["executed"].append((name, args))
            return "- 周杰伦"

        reply = await client.chat_with_tools(
            [{"role": "user", "content": "他喜欢哪个歌手"}],
            tools=[{"type": "function",
                    "function": {"name": "memory_recall", "description": "d",
                                 "parameters": {"type": "object",
                                                "properties": {}}}}],
            execute=execute)
        self.assertEqual(reply, "查到了：周杰伦")
        self.assertEqual(calls["executed"], [("memory_recall", {"query": "歌手"})])
        self.assertEqual(calls["round"], 2)

    async def test_tool_loop_final_text_without_tools(self):
        client = make_client(lambda req: ok_response("直接回答"))

        async def execute(n, a):
            return ""

        reply = await client.chat_with_tools(
            [{"role": "user", "content": "你好"}], tools=[], execute=execute)
        self.assertEqual(reply, "直接回答")


if __name__ == "__main__":
    unittest.main()
