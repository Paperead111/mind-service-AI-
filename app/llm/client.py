"""DeepSeek API 客户端。

能力：
- 重试：指数退避；429 尊重 Retry-After 头；5xx/网络错误重试，耗尽后抛 LLMError
- 超时：单次请求 timeout（settings.llm_timeout）
- 限流：每分钟滑动窗口限流（settings.llm_rate_limit_per_minute）
- JSON 结构化输出：json_mode + 解析容错（剥代码块围栏/截取花括号），
  解析失败自动追加纠正指令重试一次
"""
import asyncio
import json
import time
from collections import deque

import httpx

from app.config import settings
from app.logging_setup import get_logger

log = get_logger("llm")

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class LLMError(Exception):
    """LLM 调用失败（重试耗尽后抛出），消息面向用户可读。"""


class MinuteRateLimiter:
    """滑动窗口限流：每分钟最多 max_calls 次调用。"""

    def __init__(self, max_calls: int):
        self.max_calls = max_calls
        self._calls: deque[float] = deque()

    async def acquire(self) -> None:
        while True:
            now = time.monotonic()
            while self._calls and now - self._calls[0] > 60.0:
                self._calls.popleft()
            if len(self._calls) < self.max_calls:
                self._calls.append(now)
                return
            wait = 60.0 - (now - self._calls[0])
            log.debug("限流等待 %.1fs", wait)
            await asyncio.sleep(wait)


class DeepSeekClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model_id: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        rate_limit_per_minute: int | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.api_key = settings.deepseek_api_key if api_key is None else api_key
        self.base_url = (settings.deepseek_base_url if base_url is None else base_url).rstrip("/")
        self.model_id = settings.model_id if model_id is None else model_id
        self.timeout = settings.llm_timeout if timeout is None else timeout
        self.max_retries = settings.llm_max_retries if max_retries is None else max_retries
        self.limiter = MinuteRateLimiter(
            settings.llm_rate_limit_per_minute if rate_limit_per_minute is None else rate_limit_per_minute
        )
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def probe(self, timeout: float | None = None) -> dict:
        """R0 Key 连通性探测：轻量 models 查询（不消耗推理额度）。

        返回 {"ok": bool, "status": int|None, "detail": "ok|no_key|auth|network|http"}。
        detail=auth 表示 Key 确定无效（HTTP 401/403）→ 启动必须熔断。
        """
        if not self.api_key:
            return {"ok": False, "status": None, "detail": "no_key"}
        try:
            resp = await self._http.get("/models", timeout=timeout or 5.0)
        except httpx.HTTPError as exc:
            return {"ok": False, "status": None,
                    "detail": f"network:{type(exc).__name__}"}
        if resp.status_code == 200:
            return {"ok": True, "status": 200, "detail": "ok"}
        if resp.status_code in (401, 403):
            return {"ok": False, "status": resp.status_code, "detail": "auth"}
        return {"ok": False, "status": resp.status_code, "detail": "http"}

    async def close(self) -> None:
        await self._http.aclose()

    # ---------- 基础调用 ----------

    async def _completion(self, payload: dict) -> dict:
        """带重试/限流的底层调用，返回 API 原始 JSON。"""
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            await self.limiter.acquire()
            try:
                resp = await self._http.post("/chat/completions", json=payload)
                if resp.status_code in RETRYABLE_STATUS:
                    retry_after = 1.0
                    if resp.status_code == 429:
                        try:
                            retry_after = float(resp.headers.get("Retry-After", "1"))
                        except ValueError:
                            retry_after = 1.0
                    err = LLMError(f"服务端 {resp.status_code}")
                    last_err = err
                    if attempt < self.max_retries:
                        wait = min(retry_after + 2 ** attempt, 30.0)
                        log.warning("LLM %d 失败，%.1fs 后重试（%d/%d）",
                                    resp.status_code, wait, attempt + 1, self.max_retries)
                        await asyncio.sleep(wait)
                        continue
                    raise err
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
                last_err = exc
                if attempt < self.max_retries:
                    wait = min(2 ** attempt, 8.0)
                    log.warning("LLM 网络/解析异常，%.1fs 后重试：%s", wait, exc)
                    await asyncio.sleep(wait)
                    continue
                raise LLMError(f"调用失败：{exc}") from exc
        raise LLMError(f"重试 {self.max_retries} 次后仍失败：{last_err}")

    async def chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        """普通对话调用。messages = [{"role":"system|user|assistant","content":...}]。"""
        if not self.configured:
            raise LLMError("未配置 DEEPSEEK_API_KEY：请填入 .env 后重启服务")
        payload: dict = {
            "model": self.model_id,
            "messages": messages,
            "temperature": settings.llm_temperature if temperature is None else temperature,
            "max_tokens": settings.llm_max_tokens if max_tokens is None else max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        data = await self._completion(payload)
        return data["choices"][0]["message"]["content"]

    # ---------- 工具调用（function calling） ----------

    async def chat_round(self, messages: list[dict], tools: list[dict] | None = None,
                         temperature: float | None = None,
                         max_tokens: int | None = None) -> dict:
        """单轮调用：返回 {"content": str|None, "tool_calls": [...], "finish_reason": str}。"""
        if not self.configured:
            raise LLMError("未配置 DEEPSEEK_API_KEY：请填入 .env 后重启服务")
        payload: dict = {
            "model": self.model_id,
            "messages": messages,
            "temperature": settings.llm_temperature if temperature is None else temperature,
            "max_tokens": settings.llm_max_tokens if max_tokens is None else max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        data = await self._completion(payload)
        choice = data["choices"][0]
        message = choice.get("message") or {}
        return {
            "content": message.get("content"),
            "tool_calls": message.get("tool_calls") or [],
            "finish_reason": choice.get("finish_reason"),
        }

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        execute,
        max_rounds: int = 4,
        temperature: float | None = None,
    ) -> str:
        """工具调用循环：模型请求工具 → execute(name, args) 执行 → 结果回填 → 继续，直到给出最终回复。"""
        msgs = list(messages)
        for _ in range(max_rounds):
            r = await self.chat_round(msgs, tools=tools, temperature=temperature)
            if not r["tool_calls"]:
                return r["content"] or ""
            msgs.append({
                "role": "assistant",
                "content": r["content"] or "",
                "tool_calls": r["tool_calls"],
            })
            for tc in r["tool_calls"]:
                fn = tc.get("function") or {}
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
                result = await execute(name, args)
                msgs.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": str(result),
                })
        # 轮次用尽：取最后一条 assistant 文本兜底
        for m in reversed(msgs):
            if m.get("role") == "assistant" and m.get("content"):
                return m["content"]
        return ""

    # ---------- 结构化输出 ----------

    async def chat_json(
        self,
        messages: list[dict],
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> dict:
        """要求模型只输出 JSON；解析失败自动追加纠正指令重试一次，仍失败抛 LLMError。"""
        content = await self.chat(
            messages, temperature=temperature, max_tokens=max_tokens, json_mode=True
        )
        parsed = self._parse_json(content)
        if parsed is None:
            if not (content or "").strip():
                # 空内容：模型间歇性交白卷，重试大概率还是空 → 直接放弃，省一次调用
                raise LLMError("模型返回空内容（判断调用）")
            log.warning("JSON 解析失败（len=%d，头部=%r），追加纠正指令重试",
                        len(content or ""), (content or "")[:40])
            messages = messages + [
                {"role": "assistant", "content": content},
                {"role": "user", "content": "你的上一段回复不是合法 JSON。请重新输出：只输出 JSON 对象，不要任何其他文字。"},
            ]
            content = await self.chat(
                messages, temperature=temperature, max_tokens=max_tokens, json_mode=True
            )
            parsed = self._parse_json(content)
        if parsed is None:
            raise LLMError("模型连续两次未输出合法 JSON")
        return parsed

    @staticmethod
    def _parse_json(content: str) -> dict | None:
        text = (content or "").strip()
        # 容错 1：剥 markdown 代码块围栏
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
        try:
            obj = json.loads(text)
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            pass
        # 容错 2：截取第一个 { 到最后一个 }
        s, e = text.find("{"), text.rfind("}")
        if s != -1 and e > s:
            try:
                obj = json.loads(text[s:e + 1])
                return obj if isinstance(obj, dict) else None
            except json.JSONDecodeError:
                pass
        return None
