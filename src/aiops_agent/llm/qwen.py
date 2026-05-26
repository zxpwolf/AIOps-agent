"""通义千问 LLM Provider 实现.

对接阿里云百炼 DashScope OpenAI 兼容 API，实现 chat、complete、embed 方法，
集成 OpenTelemetry Span。

API 文档: https://help.aliyun.com/zh/model-studio/
OpenAI 兼容端点: https://dashscope.aliyuncs.com/compatible-mode/v1
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any, Optional

import aiohttp

from aiops_agent.llm.provider import ChatResponse, LLMProvider
from aiops_agent.models.schemas import Message
from aiops_agent.observability.tracing import traced

logger = logging.getLogger(__name__)

# 百炼 DashScope OpenAI 兼容端点（中国大陆）
_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_DEFAULT_MODEL = "qwen3-235b-a22b"


class QwenProvider(LLMProvider):
    """通义千问 Provider — 对接百炼 DashScope OpenAI 兼容 API."""

    def __init__(
        self,
        api_key: str,
        model: str = _DEFAULT_MODEL,
        api_base: str = _DEFAULT_BASE_URL,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        timeout_seconds: int = 120,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._api_base = api_base.rstrip("/")
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def provider_name(self) -> str:
        return "qwen"

    @traced("llm.qwen.chat")
    async def chat(self, messages: list[Message], **kwargs: Any) -> ChatResponse:
        """通义千问多轮对话（OpenAI 兼容格式）."""
        session = await self._get_session()

        payload: dict[str, Any] = {
            "model": kwargs.get("model", self._model),
            "messages": [
                {"role": m.role, "content": m.content} for m in messages
            ],
            "max_tokens": kwargs.get("max_tokens", self._max_tokens),
            "temperature": kwargs.get("temperature", self._temperature),
        }

        # qwen3 系列默认开启思考模式，可通过 extra_body 控制
        if "enable_thinking" in kwargs:
            payload["extra_body"] = {"enable_thinking": kwargs["enable_thinking"]}

        logger.info(
            "Qwen API 请求: model=%s, messages_count=%d, max_tokens=%d, temperature=%.2f",
            payload["model"],
            len(payload["messages"]),
            payload["max_tokens"],
            payload["temperature"],
        )

        async with session.post(
            f"{self._api_base}/chat/completions",
            json=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"通义千问 API 调用失败: HTTP {resp.status} - {body}")

            data = await resp.json()

        choices = data.get("choices", [])
        if not choices:
            return ChatResponse(content="", model=self._model)

        message = choices[0].get("message", {})
        content = message.get("content", "")

        # qwen3 思考模式下，reasoning_content 包含思考过程
        reasoning = message.get("reasoning_content", "")

        usage = data.get("usage", {})

        logger.info(
            "Qwen API 响应成功: model=%s, input_tokens=%d, output_tokens=%d, finish_reason=%s",
            data.get("model", self._model),
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            choices[0].get("finish_reason", ""),
        )

        return ChatResponse(
            content=content,
            model=data.get("model", self._model),
            usage={
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            finish_reason=choices[0].get("finish_reason", ""),
            metadata={"reasoning_content": reasoning} if reasoning else {},
        )

    @traced("llm.qwen.chat_stream")
    async def chat_stream(
        self, messages: list[Message], **kwargs: Any
    ) -> AsyncIterator[str]:
        """通义千问流式对话（OpenAI 兼容 SSE 格式）."""
        session = await self._get_session()

        payload: dict[str, Any] = {
            "model": kwargs.get("model", self._model),
            "messages": [
                {"role": m.role, "content": m.content} for m in messages
            ],
            "max_tokens": kwargs.get("max_tokens", self._max_tokens),
            "temperature": kwargs.get("temperature", self._temperature),
            "stream": True,
        }

        if "enable_thinking" in kwargs:
            payload["extra_body"] = {"enable_thinking": kwargs["enable_thinking"]}

        async with session.post(
            f"{self._api_base}/chat/completions",
            json=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(
                    f"通义千问流式 API 调用失败: HTTP {resp.status} - {body}"
                )

            async for raw_line in resp.content:
                line = raw_line.decode("utf-8").strip()
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[len("data:"):].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    choices = chunk.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue

    @traced("llm.qwen.complete")
    async def complete(self, prompt: str, **kwargs: Any) -> str:
        """通义千问文本补全."""
        messages = [Message(role="user", content=prompt)]
        response = await self.chat(messages, **kwargs)
        return response.content

    @traced("llm.qwen.embed")
    async def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        """通义千问文本向量化（OpenAI 兼容格式）."""
        session = await self._get_session()

        payload = {
            "model": kwargs.get("model", "text-embedding-v3"),
            "input": texts,
        }

        async with session.post(
            f"{self._api_base}/embeddings",
            json=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"通义千问 Embedding API 调用失败: HTTP {resp.status} - {body}")

            data = await resp.json()

        return [item["embedding"] for item in data.get("data", [])]

    async def close(self) -> None:
        """关闭 HTTP 会话."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session
