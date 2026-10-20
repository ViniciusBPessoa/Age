"""O `usage` que volta do provedor, traduzido para o canônico.

Os tokens de cache eram descartados aqui. O adaptador lia
`prompt_tokens` e `completion_tokens` e jogava o resto fora, então todo
prompt era precificado como se nada viesse de cache — e o hit custa 1/50
do miss.

Dois formatos convivem no mesmo wire compatível com OpenAI: o DeepSeek
manda `prompt_cache_hit_tokens` no topo do objeto, a OpenAI manda
`prompt_tokens_details.cached_tokens`. Ler só um deixaria metade dos
provedores sem desconto.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x@localhost/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("MASTER_KEY", "k" * 48)
os.environ.setdefault("SERVICE_JWT_SECRET", "s" * 48)

from app.adapters.llm.openai_compatible import (  # noqa: E402
    OpenAICompatibleProvider,
)

_parse = OpenAICompatibleProvider._parse_usage


def test_le_o_formato_do_deepseek() -> None:
    """Sabotagem: ler só `prompt_tokens_details`.

    Payload real da API: hit e miss no topo, e a soma dos dois é
    `prompt_tokens`.
    """
    usage = _parse(
        {
            "prompt_tokens": 10_000,
            "completion_tokens": 300,
            "prompt_cache_hit_tokens": 9_600,
            "prompt_cache_miss_tokens": 400,
        }
    )

    assert usage is not None
    assert usage.prompt_tokens == 10_000
    assert usage.completion_tokens == 300
    assert usage.cached_prompt_tokens == 9_600
    assert usage.uncached_prompt_tokens == 400


def test_le_o_formato_da_openai() -> None:
    """Sabotagem: ler só `prompt_cache_hit_tokens`.

    A OpenAI aninha o número em `prompt_tokens_details`.
    """
    usage = _parse(
        {
            "prompt_tokens": 2_000,
            "completion_tokens": 50,
            "prompt_tokens_details": {"cached_tokens": 1_800},
        }
    )

    assert usage is not None
    assert usage.cached_prompt_tokens == 1_800


def test_hit_mais_miss_fecha_com_o_prompt() -> None:
    """A invariante do provedor, afirmada em voz alta.

    Sabotagem: ler `prompt_cache_miss_tokens` para `cached_prompt_tokens`
    (campo trocado — o erro mais provável nesta função). Com 9.600 e
    400 trocados, a conta não fecha.

    Se um dia o provedor mudar a semântica de hit para "adicional em vez
    de subconjunto", é aqui que o teste grita — e esse é o cenário que
    tornaria o custo subestimado, a direção perigosa.
    """
    bruto = {
        "prompt_tokens": 10_000,
        "completion_tokens": 0,
        "prompt_cache_hit_tokens": 9_600,
        "prompt_cache_miss_tokens": 400,
    }
    usage = _parse(bruto)

    assert usage is not None
    assert (
        bruto["prompt_cache_hit_tokens"] + bruto["prompt_cache_miss_tokens"]
        == bruto["prompt_tokens"]
    )
    assert usage.cached_prompt_tokens == bruto["prompt_cache_hit_tokens"]


def test_provedor_que_nao_reporta_cache_fica_none() -> None:
    """Sabotagem: default `0` em vez de `None`.

    "Não informou" e "informou que nada veio de cache" são fatos
    diferentes, e só o segundo é medição. Com `0` por default, um
    provedor silencioso pareceria estar reportando cache zerado.
    """
    usage = _parse({"prompt_tokens": 500, "completion_tokens": 10})

    assert usage is not None
    assert usage.cached_prompt_tokens is None
    assert usage.uncached_prompt_tokens == 500


def test_cache_zero_e_diferente_de_ausente() -> None:
    """Provedor que reporta zero está afirmando algo, e isso é guardado.

    Sabotagem: normalizar `0` para `None`. Perderia o sinal de "o cache
    do provedor parou de pegar", que num prompt grande é diagnóstico
    real.
    """
    usage = _parse(
        {
            "prompt_tokens": 10_000,
            "completion_tokens": 0,
            "prompt_cache_hit_tokens": 0,
        }
    )

    assert usage is not None
    assert usage.cached_prompt_tokens == 0


def test_usage_ausente_devolve_none() -> None:
    """Chunk de streaming sem `usage` não inventa um zerado.

    Sabotagem: devolver `Usage(0, 0)`. No laço do orquestrador isso
    somaria uma chamada de custo zero por chunk, e o `or usage` do
    streaming perderia o uso verdadeiro que vem no último.
    """
    assert _parse(None) is None
    assert _parse({}) is None
