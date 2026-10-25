"""O custo é somado POR CHAMADA, não uma vez sobre o total do turno.

Este é o defeito que menos se vê e mais custa. O orquestrador somava os
tokens de todas as iteracoes num `Usage` só, e o custo era calculado uma
vez, no fim, sobre esse total. Enquanto o preco era uma constante, dava
no mesmo.

Com preco que depende da hora, nao da: um turno com ferramentas dura
minutos e pode atravessar a fronteira do horario de pico do provedor (no
DeepSeek, 01:00-04:00 e 06:00-10:00 UTC). Somar e multiplicar por um
preco só atribui o preco de UM instante a chamadas feitas em outro.

Consertar a funcao de preco e deixar o calculo no fim passaria todos os
outros testes desta suite e continuaria errado — e errado justamente no
turno normal do Age, que usa ferramenta quase sempre. Por isso este
arquivo existe separado.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x@localhost/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("MASTER_KEY", "k" * 48)
os.environ.setdefault("SERVICE_JWT_SECRET", "s" * 48)

import pytest  # noqa: E402

from app.adapters.llm.deepseek import (  # noqa: E402
    _FLASH_OFF,
    _FLASH_PEAK,
    _PrecoPorHorario,
)
from app.domain.orchestrator import run_conversation_turn  # noqa: E402
from app.ports.llm import (  # noqa: E402
    Chunk,
    Completion,
    LLMProvider,
    Message,
    ModelCapabilities,
    ToolSpec,
    Usage,
)

PRECO = _PrecoPorHorario(peak=_FLASH_PEAK, off_peak=_FLASH_OFF)

CAPS = ModelCapabilities(
    supports_tools=True,
    supports_parallel_tool_calls=True,
    supports_streaming=True,
    supports_system_role=True,
    supports_json_mode=True,
    context_window=1_048_576,
    max_output_tokens=8_192,
    cost_per_1m_input=0.30,
    cost_per_1m_output=1.20,
    pricing=PRECO,
)


class ProvedorDeDuasIteracoes(LLMProvider):
    """Pede uma ferramenta na primeira passada, responde na segunda.

    Cada passada reporta 1M de tokens de prompt, para o custo de cada
    uma ser exatamente o preco por 1M da faixa — assim a assercao fala
    de preco, nao de aritmetica de token.
    """

    key = "fake"
    display_name = "Fake"

    def __init__(self, caps: ModelCapabilities = CAPS) -> None:
        self._caps = caps
        self.chamadas = 0

    @property
    def capabilities(self) -> ModelCapabilities:
        return self._caps

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> Completion:
        raise NotImplementedError("o turno usa streaming")

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> AsyncIterator[Chunk]:
        self.chamadas += 1
        uso = Usage(prompt_tokens=1_000_000, completion_tokens=0)

        if self.chamadas == 1:
            yield Chunk(
                tool_call_delta={
                    "index": 0,
                    "id": "call_1",
                    "function": {"name": "get_events", "arguments": "{}"},
                }
            )
            yield Chunk(finish_reason="tool_calls", usage=uso, done=True)
            return

        yield Chunk(text_delta="Olhei sua agenda.")
        yield Chunk(finish_reason="stop", usage=uso, done=True)

    async def health_check(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


def _relogio(*instantes: datetime):
    """Relógio que avança a cada leitura, parando no último instante.

    Sem poder controlar o tempo, a travessia de faixa é impossível de
    testar: ela depende de duas chamadas caírem em horas diferentes.
    """
    sequencia = list(instantes)

    def agora() -> datetime:
        return sequencia.pop(0) if len(sequencia) > 1 else sequencia[0]

    return agora


async def _rodar(provider: LLMProvider, agora) -> object:
    return await run_conversation_turn(
        provider=provider,
        messages=[Message(role="user", content="o que tenho hoje?")],
        tools=[
            ToolSpec(
                name="get_events",
                description="lê a agenda",
                parameters={"type": "object", "properties": {}},
            )
        ],
        execute_tool=lambda nome, args: _ok(),
        emit_text=lambda _t: _nada(),
        emit_tool_start=lambda _n: _nada(),
        emit_tool_end=lambda _n, _ok, _s, _f=None: _nada(),
        is_cancelled=_falso,
        now=agora,
    )


async def _ok() -> str:
    return "[]"


async def _nada() -> None:
    return None


async def _falso() -> bool:
    return False


async def test_turno_que_atravessa_o_pico_cobra_cada_metade_no_seu_preco() -> None:
    """A sabotagem que SÓ este teste pega.

    Sabotagem: voltar `run_turn`/orquestrador a calcular o custo uma vez
    no fim, sobre `result.usage`. O total é 2M de tokens de prompt; com
    um preço só, o resultado seria 0,60 (tudo no pico) ou 0,30 (tudo
    fora) — nunca 0,45.

    Duas chamadas: a primeira às 09:59 UTC (pico, 0,30/1M), a segunda às
    10:01 (fora, 0,15/1M). O certo é a soma das duas, não o total vezes
    um preço.
    """
    provider = ProvedorDeDuasIteracoes()
    agora = _relogio(
        datetime(2026, 10, 20, 9, 59, tzinfo=UTC),
        datetime(2026, 10, 20, 10, 1, tzinfo=UTC),
    )

    resultado = await _rodar(provider, agora)

    assert provider.chamadas == 2
    assert resultado.usage.prompt_tokens == 2_000_000
    assert resultado.cost_usd == pytest.approx(0.30 + 0.15)
    # E não o que um cálculo único daria:
    assert resultado.cost_usd != pytest.approx(0.60)
    assert resultado.cost_usd != pytest.approx(0.30)


async def test_turno_inteiro_fora_de_pico_soma_as_duas_chamadas() -> None:
    """Sabotagem: somar só a última chamada (perder o acúmulo).

    Com as duas chamadas na mesma faixa, o total ainda tem de ser a
    soma — 0,30, não 0,15. É o caso comum, e é o que pegaria um `=` no
    lugar de um `+=`.
    """
    provider = ProvedorDeDuasIteracoes()
    agora = _relogio(datetime(2026, 10, 20, 23, 0, tzinfo=UTC))

    resultado = await _rodar(provider, agora)

    assert resultado.cost_usd == pytest.approx(0.15 * 2)


async def test_modelo_sem_preco_devolve_none_e_nao_zero() -> None:
    """Sabotagem: inicializar o acumulador em `0.0`.

    Com `0.0`, um modelo local passaria a reportar custo zero — que
    afirma gratuidade em vez de admitir desconhecimento, e envenena a
    média do painel.
    """
    sem_preco = ModelCapabilities(
        supports_tools=True,
        supports_parallel_tool_calls=False,
        supports_streaming=True,
        supports_system_role=True,
        supports_json_mode=False,
        context_window=8_192,
        max_output_tokens=2_048,
    )
    provider = ProvedorDeDuasIteracoes(sem_preco)

    resultado = await _rodar(
        provider, _relogio(datetime(2026, 10, 20, 23, 0, tzinfo=UTC))
    )

    assert resultado.cost_usd is None
