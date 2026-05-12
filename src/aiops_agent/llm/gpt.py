"""GPT LLM Provider 骨架实现.

对接 OpenAI GPT API，实现 chat、complete 方法的基本对接。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import aiohttp

from aiops_agent.llm.provider import ChatResponse, LLMProvider
from aiops_agent.models.schemas import Message
from aiops_agent.observability.tracing import traced

logger = logging.getLogger(__name__)


class GPTProvider(LLMProvider):
    """GPT Provider — 对接 OpenAI API."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4",
        api_base: str = "https://api.openai.com/v1",
        max_tokens: int = 4096,
        temperature: float = 0.7,
        timeout_seconds: int = 60,
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
        return "gpt"

    @traced("llm.gpt.chat")
    async def chat(self, messages: list[Message], **kwargs: Any) -> ChatResponse:
        """GPT 多轮对话."""
        session = await self._get_session()

        payload = {
            "model": kwargs.get("model", self._model),
            "max_tokens": kwargs.get("max_tokens", self._max_tokens),
            "temperature": kwargs.get("temperature", self._temperature),
            "messages": [
                {"role": m.role, "content": m.content} for m in messages
            ],
        }

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
                raise RuntimeError(f"GPT API 调用失败: HTTP {resp.status} - {body}")

            data = await resp.json()

        choices = data.get("choices", [])
        content = choices[0]["message"]["content"] if choices else ""
        usage = data.get("usage", {})

        return ChatResponse(
            content=content,
            model=data.get("model", self._model),
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            finish_reason=choices[0].get("finish_reason", "") if choices else "",
        )

    @traced("llm.gpt.complete")
    async def complete(self, prompt: str, **kwargs: Any) -> str:
        """GPT 文本补全."""
        messages = [Message(role="user", content=prompt)]
        response = await self.chat(messages, **kwargs)
        return response.content

    async def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        """GPT Embedding."""
        session = await self._get_session()

        payload = {
            "model": kwargs.get("model", "text-embedding-3-small"),
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
                raise RuntimeError(f"GPT Embedding API 调用失败: HTTP {resp.status} - {body}")

            data = await resp.json()

        return [item["embedding"] for item in data.get("data", [])]

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session
