"""LLM_Provider 抽象接口 — 模型无关的大语言模型调用层.

定义 LLMProvider 抽象基类，支持通义千问、Claude、GPT 等多种模型后端，
通过配置文件切换，并实现自动降级逻辑。
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from collections.abc import AsyncIterator
from typing import Any, Optional

from aiops_agent.models.schemas import Message
from aiops_agent.observability.metrics_store import LLMCallEvent

logger = logging.getLogger(__name__)


@dataclass
class ChatResponse:
    """LLM 对话响应."""

    content: str
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class LLMProvider(ABC):
    """LLM Provider 抽象基类.

    所有 LLM 后端实现此接口，提供统一的 chat、complete、embed 方法。
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Provider 名称标识."""
        ...

    @abstractmethod
    async def chat(self, messages: list[Message], **kwargs: Any) -> ChatResponse:
        """多轮对话接口.

        Args:
            messages: 对话消息列表。
            **kwargs: 额外参数（temperature、max_tokens 等）。

        Returns:
            ChatResponse 包含模型回复。
        """
        ...

    @abstractmethod
    async def complete(self, prompt: str, **kwargs: Any) -> str:
        """文本补全接口.

        Args:
            prompt: 输入提示。
            **kwargs: 额外参数。

        Returns:
            补全后的文本。
        """
        ...

    @abstractmethod
    async def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        """文本向量化接口.

        Args:
            texts: 待向量化的文本列表。
            **kwargs: 额外参数。

        Returns:
            向量列表。
        """
        ...

    async def chat_stream(
        self, messages: list[Message], **kwargs: Any
    ) -> AsyncIterator[str]:
        """流式对话接口 — 逐 token 返回.

        默认实现: 回退到非流式 chat()，将完整内容作为单个 chunk 返回。
        子类可覆盖以实现真正的流式响应。
        """
        response = await self.chat(messages, **kwargs)
        yield response.content

    async def close(self) -> None:
        """关闭 Provider，释放资源."""


class LLMProviderFactory:
    """LLM Provider 工厂，支持通过配置切换和自动降级.

    使用方式::

        factory = LLMProviderFactory()
        factory.register("qwen", qwen_provider)
        factory.register("claude", claude_provider)
        factory.set_primary("qwen")
        factory.set_fallback("claude")

        response = await factory.chat(messages)  # 自动降级
    """

    def __init__(self) -> None:
        self._providers: dict[str, LLMProvider] = {}
        self._primary_name: Optional[str] = None
        self._fallback_name: Optional[str] = None
        self._pending_llm_calls: list[LLMCallEvent] = []

    def register(self, name: str, provider: LLMProvider) -> None:
        """注册 LLM Provider."""
        self._providers[name] = provider
        logger.info("LLM Provider 已注册: %s", name)

    def set_primary(self, name: str) -> None:
        """设置主 Provider."""
        if name not in self._providers:
            raise ValueError(f"Provider '{name}' 未注册")
        self._primary_name = name

    def set_fallback(self, name: str) -> None:
        """设置备用 Provider."""
        if name not in self._providers:
            raise ValueError(f"Provider '{name}' 未注册")
        self._fallback_name = name

    def get_provider(self, name: str) -> LLMProvider:
        """获取指定 Provider."""
        provider = self._providers.get(name)
        if provider is None:
            raise ValueError(f"Provider '{name}' 未注册")
        return provider

    @property
    def primary(self) -> LLMProvider:
        """获取主 Provider."""
        if self._primary_name is None:
            raise ValueError("未设置主 Provider")
        return self._providers[self._primary_name]

    async def chat(self, messages: list[Message], **kwargs: Any) -> ChatResponse:
        """调用 LLM 对话，主 Provider 失败时自动降级到备用 Provider.

        Raises:
            RuntimeError: 所有 Provider 均失败时抛出。
        """
        # 尝试主 Provider
        if self._primary_name:
            provider_name = self._primary_name
            call_start = time.monotonic()
            try:
                logger.info(
                    "调用主 LLM Provider: name=%s, messages_count=%d",
                    self._primary_name,
                    len(messages),
                )
                response = await self._providers[self._primary_name].chat(messages, **kwargs)
                call_duration = (time.monotonic() - call_start) * 1000
                self._pending_llm_calls.append(LLMCallEvent(
                    provider=provider_name,
                    model=response.model,
                    tokens_in=response.usage.get("input_tokens", 0) or response.usage.get("prompt_tokens", 0),
                    tokens_out=response.usage.get("output_tokens", 0) or response.usage.get("completion_tokens", 0),
                    duration_ms=call_duration,
                    success=True,
                ))
                return response
            except Exception as exc:
                call_duration = (time.monotonic() - call_start) * 1000
                self._pending_llm_calls.append(LLMCallEvent(
                    provider=provider_name,
                    model="unknown",
                    tokens_in=0,
                    tokens_out=0,
                    duration_ms=call_duration,
                    success=False,
                    error=str(exc),
                ))
                logger.warning(
                    "主 LLM Provider '%s' 调用失败: %s，尝试降级",
                    self._primary_name,
                    exc,
                )

        # 尝试备用 Provider
        if self._fallback_name and self._fallback_name != self._primary_name:
            provider_name = self._fallback_name
            call_start = time.monotonic()
            try:
                logger.info(
                    "调用备用 LLM Provider: name=%s",
                    self._fallback_name,
                )
                response = await self._providers[self._fallback_name].chat(messages, **kwargs)
                call_duration = (time.monotonic() - call_start) * 1000
                self._pending_llm_calls.append(LLMCallEvent(
                    provider=provider_name,
                    model=response.model,
                    tokens_in=response.usage.get("input_tokens", 0) or response.usage.get("prompt_tokens", 0),
                    tokens_out=response.usage.get("output_tokens", 0) or response.usage.get("completion_tokens", 0),
                    duration_ms=call_duration,
                    success=True,
                ))
                return response
            except Exception as exc:
                call_duration = (time.monotonic() - call_start) * 1000
                self._pending_llm_calls.append(LLMCallEvent(
                    provider=provider_name,
                    model="unknown",
                    tokens_in=0,
                    tokens_out=0,
                    duration_ms=call_duration,
                    success=False,
                    error=str(exc),
                ))
                logger.error(
                    "备用 LLM Provider '%s' 调用也失败: %s",
                    self._fallback_name,
                    exc,
                )

        logger.error("所有 LLM Provider 均不可用: primary=%s, fallback=%s", self._primary_name, self._fallback_name)
        raise RuntimeError("所有 LLM Provider 均不可用")

    async def chat_stream(
        self, messages: list[Message], **kwargs: Any
    ) -> AsyncIterator[str]:
        """流式调用 LLM 对话，主 Provider 失败时自动降级到备用 Provider."""
        if self._primary_name:
            provider_name = self._primary_name
            call_start = time.monotonic()
            try:
                async for token in self._providers[self._primary_name].chat_stream(
                    messages, **kwargs
                ):
                    yield token
                call_duration = (time.monotonic() - call_start) * 1000
                self._pending_llm_calls.append(LLMCallEvent(
                    provider=provider_name,
                    model="unknown",
                    tokens_in=0,
                    tokens_out=0,
                    duration_ms=call_duration,
                    success=True,
                ))
                return
            except Exception as exc:
                call_duration = (time.monotonic() - call_start) * 1000
                self._pending_llm_calls.append(LLMCallEvent(
                    provider=provider_name,
                    model="unknown",
                    tokens_in=0,
                    tokens_out=0,
                    duration_ms=call_duration,
                    success=False,
                    error=str(exc),
                ))
                logger.warning(
                    "主 LLM Provider '%s' 流式调用失败: %s，尝试降级",
                    self._primary_name,
                    exc,
                )

        if self._fallback_name and self._fallback_name != self._primary_name:
            provider_name = self._fallback_name
            call_start = time.monotonic()
            try:
                async for token in self._providers[self._fallback_name].chat_stream(
                    messages, **kwargs
                ):
                    yield token
                call_duration = (time.monotonic() - call_start) * 1000
                self._pending_llm_calls.append(LLMCallEvent(
                    provider=provider_name,
                    model="unknown",
                    tokens_in=0,
                    tokens_out=0,
                    duration_ms=call_duration,
                    success=True,
                ))
                return
            except Exception as exc:
                call_duration = (time.monotonic() - call_start) * 1000
                self._pending_llm_calls.append(LLMCallEvent(
                    provider=provider_name,
                    model="unknown",
                    tokens_in=0,
                    tokens_out=0,
                    duration_ms=call_duration,
                    success=False,
                    error=str(exc),
                ))
                logger.error(
                    "备用 LLM Provider '%s' 流式调用也失败: %s",
                    self._fallback_name,
                    exc,
                )

        raise RuntimeError("所有 LLM Provider 均不可用")

    async def complete(self, prompt: str, **kwargs: Any) -> str:
        """调用文本补全，支持自动降级."""
        if self._primary_name:
            try:
                return await self._providers[self._primary_name].complete(prompt, **kwargs)
            except Exception as exc:
                logger.warning(
                    "主 LLM Provider '%s' 补全失败: %s，尝试降级",
                    self._primary_name,
                    exc,
                )

        if self._fallback_name and self._fallback_name != self._primary_name:
            try:
                return await self._providers[self._fallback_name].complete(prompt, **kwargs)
            except Exception as exc:
                logger.error(
                    "备用 LLM Provider '%s' 补全也失败: %s",
                    self._fallback_name,
                    exc,
                )

        raise RuntimeError("所有 LLM Provider 均不可用")

    def get_pending_llm_calls(self) -> list[LLMCallEvent]:
        return list(self._pending_llm_calls)

    def clear_pending_llm_calls(self) -> None:
        self._pending_llm_calls.clear()

    async def close(self) -> None:
        """关闭所有 Provider."""
        for name, provider in self._providers.items():
            try:
                await provider.close()
            except Exception:
                logger.exception("关闭 Provider '%s' 失败", name)
