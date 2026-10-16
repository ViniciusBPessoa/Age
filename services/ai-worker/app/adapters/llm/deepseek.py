"""Adaptador DeepSeek.

A API é compatível com a da OpenAI, então só metadados mudam.

## Os nomes de modelo mudaram, e os preços com eles

Medido contra a API real em out/2026, com `GET /v1/models`: os modelos
servidos hoje são **`deepseek-flash`** (DeepSeek-V4.1-Flash) e
**`deepseek-v4-pro`**. `deepseek-chat` e `deepseek-reasoner`, que este
arquivo usava, **não aparecem mais na lista** — mas continuam aceitos e
são redirecionados: um pedido com `model: "deepseek-chat"` volta com
`"model": "deepseek-flash"` no corpo da resposta.

Por isso os nomes antigos seguem mapeados aqui. Removê-los quebraria as
conversas de quem já escolheu um deles em Configurações — o valor fica
gravado em `core.conversations.model`, e um nome que o adaptador não
conhece cairia em `_fallback_capabilities` com preço errado.

**Preço por 1M de tokens, medido na tabela oficial:**

| Modelo | Entrada (fora de pico / pico) | Saída (fora de pico / pico) |
|---|---|---|
| `deepseek-flash` | 0,15 / 0,30 | 0,60 / 1,20 |
| `deepseek-v4-pro` | 0,66 / 1,32 | 1,98 / 3,96 |

Os valores anteriores (0,27/1,10 e 0,55/2,19) eram de uma tabela
antiga. Isso não é cosmético: o custo estimado alimenta a cota mensal
de §3451, então um preço errado faz a cota barrar cedo ou tarde demais.

**Registramos o preço de PICO, o maior.** O DeepSeek cobra metade fora
do horário de pico (pico = 01:00–04:00 e 06:00–10:00 UTC em dias de
semana), e o preço real de um turno depende da hora em que ele rodou.
Como a cota existe para impedir surpresa na fatura, errar para cima é o
lado seguro: a estimativa vira um teto, nunca um número que subestima o
que o operador vai pagar.

## Thinking mode

Os dois modelos pensam por padrão (`effort: high`), e isso **se cobra
como token de saída**. Medido com a mesma pergunta e as mesmas tools:

| Esforço | Tokens de saída |
|---|---|
| `high` (padrão) | 501 |
| `low` | 118 |
| desabilitado | 72 |

As três chamaram a ferramenta corretamente. Não mexemos no padrão aqui
porque a qualidade do raciocínio é o que faz o planejamento de §5.5
prestar, e 500 tokens de saída custam US$ 0,0006 no pico — mas fica
registrado onde economizar se a conta apertar.

Uma restrição real do thinking, da referência da API: `tool_choice`
`required` ou nomeado devolve **400** com o thinking ligado. O
orquestrador não os usa (deixa o modelo decidir), então não há conflito
hoje; quem for usar precisa desligar o thinking antes.
"""

from __future__ import annotations

from app.adapters.llm.openai_compatible import OpenAICompatibleProvider
from app.adapters.llm.registry import register_llm
from app.ports.llm import ModelCapabilities

# Contexto e saída vêm de `GET /v1/models`, não de estimativa: 1.048.576
# e 393.216 para os dois modelos.
#
# `max_output_tokens` aqui é o que o orquestrador passa como `max_tokens`
# em cada chamada, e NÃO usamos o teto de 393k de propósito: ele é
# reserva de contexto no cálculo de janela (§5.1), e reservar 393k num
# contexto de 1M deixaria pouco espaço para o histórico. 8.192 cabe o
# thinking (medido: ~500 tokens) com folga larga.
_FLASH = ModelCapabilities(
    supports_tools=True,
    supports_parallel_tool_calls=True,
    supports_streaming=True,
    supports_system_role=True,
    supports_json_mode=True,
    context_window=1_048_576,
    max_output_tokens=8_192,
    cost_per_1m_input=0.30,
    cost_per_1m_output=1.20,
)

_PRO = ModelCapabilities(
    supports_tools=True,
    supports_parallel_tool_calls=True,
    supports_streaming=True,
    supports_system_role=True,
    supports_json_mode=True,
    context_window=1_048_576,
    max_output_tokens=8_192,
    cost_per_1m_input=1.32,
    cost_per_1m_output=3.96,
)


@register_llm
class DeepSeekProvider(OpenAICompatibleProvider):
    key = "deepseek"
    display_name = "DeepSeek"
    api_base = "https://api.deepseek.com/v1"

    # Só os nomes atuais são oferecidos na UI: apresentar um nome
    # obsoleto como opção seria convidar a escolher o que o provedor já
    # aposentou.
    known_models = ("deepseek-flash", "deepseek-v4-pro")
    default_model = "deepseek-flash"

    _capabilities_by_model = {
        "deepseek-flash": _FLASH,
        "deepseek-v4-pro": _PRO,
        # Aceitos pela API por redirecionamento, e mantidos aqui para que
        # uma conversa antiga continue com o preço certo. Fora de
        # `known_models` porque não devem ser oferecidos a quem escolhe
        # agora.
        "deepseek-chat": _FLASH,
        "deepseek-reasoner": _PRO,
        "deepseek-v4-flash": _FLASH,
    }
    _fallback_capabilities = _FLASH
