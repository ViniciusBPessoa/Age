"""Adaptador DeepSeek.

A API é compatível com a da OpenAI, então só metadados mudam. Preços de
out/2026, usados para o custo estimado que aparece no onboarding (§8.1)
e em Configurações → Uso.
"""

from __future__ import annotations

from app.adapters.llm.openai_compatible import OpenAICompatibleProvider
from app.adapters.llm.registry import register_llm
from app.ports.llm import ModelCapabilities

_CHAT = ModelCapabilities(
    supports_tools=True,
    supports_parallel_tool_calls=True,
    supports_streaming=True,
    supports_system_role=True,
    supports_json_mode=True,
    context_window=64_000,
    max_output_tokens=8_192,
    cost_per_1m_input=0.27,
    cost_per_1m_output=1.10,
)

# O modelo de raciocínio não aceita tools nem temperature. Declarar isso
# é o que faz o orquestrador se adaptar sozinho (§3.1.2) em vez de
# receber erro da API.
_REASONER = ModelCapabilities(
    supports_tools=False,
    supports_parallel_tool_calls=False,
    supports_streaming=True,
    supports_system_role=True,
    supports_json_mode=False,
    context_window=64_000,
    max_output_tokens=8_192,
    cost_per_1m_input=0.55,
    cost_per_1m_output=2.19,
)


@register_llm
class DeepSeekProvider(OpenAICompatibleProvider):
    key = "deepseek"
    display_name = "DeepSeek"
    api_base = "https://api.deepseek.com/v1"
    known_models = ("deepseek-chat", "deepseek-reasoner")
    default_model = "deepseek-chat"

    _capabilities_by_model = {
        "deepseek-chat": _CHAT,
        "deepseek-reasoner": _REASONER,
    }
    _fallback_capabilities = _CHAT
