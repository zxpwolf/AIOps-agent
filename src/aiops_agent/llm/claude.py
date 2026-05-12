"""Claude LLM Provider 骨架实现.

对接 Anthropic Claude API，实现 chat、complete 方法的基本对接。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import aiohttp

from aiops_agent.llm.provider import ChatResponse, LLMProvider
from aiops_agent.models.schemas import Message
from aiops_agent.observability.tracing import traced

logger = logging.getLogger(__name__)


class ClaudeProvider(LLMProvider):
    """Claude Provider — 对接 Anthropic API."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-3-sonnet-20240229",
        api_base: str = "https://api.anthropic.com/v1",
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
        return "claude"

    @traced("llm.claude.chat")
    async def chat(self, messages: list[Message], **kwargs: Any) -> ChatResponse:
        """Claude 多轮对话."""
        session = await self._get_session()

        # 分离 system 消息
        system_msg = ""
        chat_messages = []
        for m in messages:
            if m.role == "system":
                system_msg = m.content
            else:
                chat_messages.append({"role": m.role, "content": m.content})

        payload: dict[str, Any] = {
            "model": kwargs.get("model", self._model),
            "max_tokens": kwargs.get("max_tokens", self._max_tokens),
            "temperature": kwargs.get("temperature", self._temperature),
            "messages": chat_messages,
        }
        if system_msg:
            payload["system"] = system_msg

        async with session.post(
            f"{self._api_base}/messages",
            json=payload,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"Claude API 调用失败: HTTP {resp.status} - {body}")

            data = await resp.json()

        content_blocks = data.get("content", [])
        text = "".join(
            block.get("text", "") for block in content_blocks if block.get("type") == "text"
        )
        usage = data.get("usage", {})

        return ChatResponse(
            content=text,
            model=data.get("model", self._model),
            usage={
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
            },
            finish_reason=data.get("stop_reason", ""),
        )

    @traced("llm.claude.complete")
    async def complete(self, prompt: str, **kwargs: Any) -> str:
        """Claude 文本补全."""
        messages = [Message(role="user", content=prompt)]
        response = await self.chat(messages, **kwargs)
        return response.content

    async def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        """Claude 不原生支持 embedding，抛出 NotImplementedError."""
        raise NotImplementedError("Claude 不提供原生 Embedding API，请使用其他 Provider。")

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session
