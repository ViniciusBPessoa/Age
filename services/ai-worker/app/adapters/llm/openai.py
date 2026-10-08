"""Adaptador OpenAI.

É o segundo provedor do M1, e entra cedo de propósito: uma porta com um
adaptador só é porta não testada (ARQUITETURA.md §13 M1). Mesmo sendo
compatível com a base, ele exercita `normalize_for` numa troca real.
"""

from __future__ import annotations

from app.adapters.llm.openai_compatible import OpenAICompatibleProvider
from app.adapters.llm.registry import register_llm
from app.ports.llm import ModelCapabilities

_GPT4O = ModelCapabilities(
    supports_tools=True,
    supports_parallel_tool_calls=True,
    supports_streaming=True,
    supports_system_role=True,
    supports_json_mode=True,
    context_window=128_000,
    max_output_tokens=16_384,
    cost_per_1m_input=2.50,
    cost_per_1m_output=10.00,
)

_GPT4O_MINI = ModelCapabilities(
    supports_tools=True,
    supports_parallel_tool_calls=True,
    supports_streaming=True,
    supports_system_role=True,
    supports_json_mode=True,
    context_window=128_000,
    max_output_tokens=16_384,
    cost_per_1m_input=0.15,
    cost_per_1m_output=0.60,
)


@register_llm
class OpenAIProvider(OpenAICompatibleProvider):
    key = "openai"
    display_name = "OpenAI"
    api_base = "https://api.openai.com/v1"
    known_models = ("gpt-4o", "gpt-4o-mini")
    # mini como default: 16x mais barato e suficiente para planejar.
    default_model = "gpt-4o-mini"

    _capabilities_by_model = {
        "gpt-4o": _GPT4O,
        "gpt-4o-mini": _GPT4O_MINI,
    }
    _fallback_capabilities = _GPT4O_MINI
