"""Base para provedores com API compatível com a da OpenAI.

Serve DeepSeek e OpenAI com o mesmo código: só mudam `base_url`, modelos
e preços. Ollama também, quando o modelo suporta tools.

ARQUITETURA.md §3.1.1. A tradução que importa aqui:

- `arguments` chega como **string JSON** e precisa de `json.loads`. Se
  vier truncado (acontece com `finish_reason: "length"`), levantamos
  `MalformedToolCall` em vez de propagar dict meia-boca.
- No streaming, os deltas de tool call vêm **fragmentados por índice** e
  precisam ser concatenados antes de parsear.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.ports.llm import (
    AuthenticationError,
    Chunk,
    Completion,
    ContextTooLong,
    LLMError,
    LLMProvider,
    MalformedToolCall,
    Message,
    ModelCapabilities,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimitError,
    ToolCall,
    ToolSpec,
    Usage,
)

log = logging.getLogger("ai-worker.llm")

_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)


class OpenAICompatibleProvider(LLMProvider):
    """Implementação compartilhada. Subclasses só declaram metadados."""

    api_base: str = ""
    _capabilities_by_model: dict[str, ModelCapabilities] = {}
    _fallback_capabilities: ModelCapabilities

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model or self.default_model
        self._base_url = (base_url or self.api_base).rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=_TIMEOUT,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> ModelCapabilities:
        return self._capabilities_by_model.get(
            self._model, self._fallback_capabilities
        )

    # -----------------------------------------------------------
    # Tradução canônico -> wire
    # -----------------------------------------------------------
    def _encode_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == "tool":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": msg.tool_call_id,
                        "content": msg.content or "",
                    }
                )
                continue

            item: dict[str, Any] = {"role": msg.role}
            if msg.content is not None:
                item["content"] = msg.content
            if msg.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            # A API espera string, não objeto.
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in msg.tool_calls
                ]
                # Com tool_calls, `content` precisa existir mesmo vazio.
                item.setdefault("content", None)
            out.append(item)
        return out

    @staticmethod
    def _encode_tools(tools: list[ToolSpec] | None) -> list[dict] | None:
        if not tools:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]

    @staticmethod
    def _parse_arguments(raw: str, name: str) -> dict:
        if not raw or not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MalformedToolCall(
                f"argumentos inválidos para '{name}': JSON malformado"
            ) from exc
        if not isinstance(parsed, dict):
            raise MalformedToolCall(
                f"argumentos de '{name}' não são um objeto JSON"
            )
        return parsed

    # -----------------------------------------------------------
    # Erros HTTP -> erros tipados
    # -----------------------------------------------------------
    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return

        try:
            detail = response.json().get("error", {}).get("message", response.text)
        except Exception:  # noqa: BLE001
            detail = response.text[:300]

        if response.status_code in (401, 403):
            raise AuthenticationError(
                f"credencial rejeitada pelo {self.display_name}: {detail}",
                provider=self.key,
            )
        if response.status_code == 429:
            raise RateLimitError(
                f"{self.display_name} limitou a taxa de requisições",
                provider=self.key,
            )
        if response.status_code == 400 and "context" in detail.lower():
            raise ContextTooLong(
                f"conversa longa demais para {self._model}", provider=self.key
            )
        if response.status_code >= 500:
            raise ProviderUnavailable(
                f"{self.display_name} indisponível ({response.status_code})",
                provider=self.key,
            )
        raise LLMError(
            f"{self.display_name} recusou a requisição: {detail}", provider=self.key
        )

    # -----------------------------------------------------------
    # complete
    # -----------------------------------------------------------
    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> Completion:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": self._encode_messages(messages),
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        encoded_tools = self._encode_tools(tools)
        if encoded_tools and self.capabilities.supports_tools:
            payload["tools"] = encoded_tools

        try:
            response = await self._client.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(
                f"{self.display_name} não respondeu no tempo esperado",
                provider=self.key,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(
                f"falha de rede ao chamar {self.display_name}", provider=self.key
            ) from exc

        self._raise_for_status(response)
        data = response.json()
        choice = data["choices"][0]
        raw_message = choice["message"]

        calls: list[ToolCall] = []
        for tc in raw_message.get("tool_calls") or []:
            fn = tc.get("function", {})
            calls.append(
                ToolCall(
                    id=tc.get("id", ""),
                    name=fn.get("name", ""),
                    arguments=self._parse_arguments(
                        fn.get("arguments", ""), fn.get("name", "?")
                    ),
                )
            )

        usage_raw = data.get("usage") or {}
        return Completion(
            message=Message(
                role="assistant",
                content=raw_message.get("content"),
                tool_calls=tuple(calls),
            ),
            usage=Usage(
                prompt_tokens=usage_raw.get("prompt_tokens", 0),
                completion_tokens=usage_raw.get("completion_tokens", 0),
            ),
            finish_reason=choice.get("finish_reason", "stop"),
        )

    # -----------------------------------------------------------
    # stream
    # -----------------------------------------------------------
    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> AsyncIterator[Chunk]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": self._encode_messages(messages),
            "temperature": temperature,
            "stream": True,
            # Pede uso no último chunk; provedores que ignoram não quebram.
            "stream_options": {"include_usage": True},
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        encoded_tools = self._encode_tools(tools)
        if encoded_tools and self.capabilities.supports_tools:
            payload["tools"] = encoded_tools

        try:
            async with self._client.stream(
                "POST", "/chat/completions", json=payload
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    self._raise_for_status(response)

                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line.removeprefix("data: ").strip()
                    if data_str == "[DONE]":
                        break

                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        log.warning("chunk SSE inválido ignorado")
                        continue

                    usage_raw = event.get("usage")
                    usage = (
                        Usage(
                            prompt_tokens=usage_raw.get("prompt_tokens", 0),
                            completion_tokens=usage_raw.get("completion_tokens", 0),
                        )
                        if usage_raw
                        else None
                    )

                    choices = event.get("choices") or []
                    if not choices:
                        if usage:
                            yield Chunk(usage=usage)
                        continue

                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    finish = choice.get("finish_reason")

                    if delta.get("content"):
                        yield Chunk(text_delta=delta["content"])

                    # Deltas de tool call vêm fragmentados por índice; o
                    # orquestrador acumula (§5.1).
                    for tc_delta in delta.get("tool_calls") or []:
                        yield Chunk(tool_call_delta=tc_delta)

                    if finish:
                        yield Chunk(finish_reason=finish, usage=usage, done=True)

        except httpx.TimeoutException as exc:
            raise ProviderTimeout(
                f"{self.display_name} parou de responder no meio da geração",
                provider=self.key,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(
                f"falha de rede durante streaming do {self.display_name}",
                provider=self.key,
            ) from exc

    # -----------------------------------------------------------
    async def health_check(self) -> bool:
        """Uma mensagem mínima: valida credencial sem custo relevante."""
        try:
            await self.complete(
                [Message(role="user", content="ok")],
                temperature=0.0,
                max_tokens=1,
            )
            return True
        except AuthenticationError:
            raise
        except LLMError:
            return False

    async def aclose(self) -> None:
        await self._client.aclose()
