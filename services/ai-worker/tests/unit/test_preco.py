"""O preço de uma chamada: cache, horário de pico, e o teto.

O custo gravado por turno era maior que o real por dois motivos
independentes, e os dois foram medidos contra a tabela oficial da
DeepSeek:

1. O código aplicava o preço de PICO sempre. Fora do pico a cobrança é
   metade, e o Age é usado à noite — quase sempre fora de pico.
2. O token de prompt servido de cache custa 1/50 do token lido do zero
   (0,003 contra 0,15 por milhão), e o adaptador descartava essa
   informação. Como o prompt do Age é grande e quase igual entre
   turnos, era a maior parte da conta sendo cobrada pelo preço errado.

Cada teste aqui tem no cabeçalho a sabotagem que precisa derrubá-lo.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x@localhost/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("MASTER_KEY", "k" * 48)
os.environ.setdefault("SERVICE_JWT_SECRET", "s" * 48)

import pytest  # noqa: E402

from app.adapters.llm.deepseek import (  # noqa: E402
    _FLASH,
    _FLASH_OFF,
    _FLASH_PEAK,
    _PRO,
    _PrecoPorHorario,
)
from app.ports.llm import (  # noqa: E402
    FlatPricing,
    ModelCapabilities,
    Price,
    Usage,
)

# Uma terça-feira, para os casos de dia de semana não dependerem de
# qual dia era quando o teste foi escrito.
TERCA = datetime(2026, 10, 20, tzinfo=UTC)


def _em(hora: int, minuto: int = 0, *, dia: int = 20) -> datetime:
    """Um instante UTC de outubro de 2026. Dia 20 é terça."""
    return datetime(2026, 10, dia, hora, minuto, tzinfo=UTC)


# ---------------------------------------------------------------
# Cache
# ---------------------------------------------------------------
def test_cache_hit_barateia_o_prompt() -> None:
    """Sabotagem que derruba:

    - descartar `cached_prompt_tokens` (passar None) → os dois custos
      ficam iguais e a razão cai;
    - contar o prompt inteiro como miss E o hit por cima → o valor
      exato cai;
    - fazer o hit cair no preço do miss → a razão cai.

    O valor exato e a razão juntos: só a razão passaria com um desconto
    de 10% aplicado a tudo; só o valor exato quebra em mudança legítima
    de tabela.
    """
    com_cache = Usage(10_000, 0, cached_prompt_tokens=9_000)
    sem_cache = Usage(10_000, 0)

    custo_com = _FLASH_PEAK.cost_usd(com_cache)
    custo_sem = _FLASH_PEAK.cost_usd(sem_cache)

    assert custo_com < custo_sem
    # Com 90% do prompt cacheado, a economia fica em ~8,5x: os 10%
    # restantes continuam a preço cheio e dominam a conta. O teto de
    # 50x só vale para prompt 100% cacheado, e é o que o teste de
    # subconjunto cobre.
    assert custo_sem / custo_com > 8

    # 1.000 miss a 0,30/1M + 9.000 hit a 0,006/1M
    esperado = 1_000 / 1_000_000 * 0.30 + 9_000 / 1_000_000 * 0.006
    assert custo_com == pytest.approx(esperado)


def test_o_hit_e_subconjunto_do_prompt_nao_um_adicional() -> None:
    """Sabotagem: somar hit + prompt em vez de subtrair.

    Se o hit fosse tratado como adicional, um prompt 100% cacheado
    custaria MAIS que um sem cache nenhum — o sinal trocado do defeito
    que este trabalho conserta.
    """
    todo_cacheado = Usage(10_000, 0, cached_prompt_tokens=10_000)

    assert todo_cacheado.uncached_prompt_tokens == 0
    # 10.000 hit a 0,006/1M, e nada no preço de miss.
    assert _FLASH_PEAK.cost_usd(todo_cacheado) == pytest.approx(
        10_000 / 1_000_000 * 0.006
    )


def test_provedor_incoerente_nao_gera_custo_negativo() -> None:
    """Sabotagem: remover o `max(0, ...)` de `uncached_prompt_tokens`.

    Se um provedor reportar mais hit que prompt (bug dele, ou mudança
    de semântica), subtrair daria negativo e o custo do turno
    DIMINUIRIA o total do mês. Custo superestimado é ruim; custo
    negativo corrompe o relatório.
    """
    absurdo = Usage(1_000, 0, cached_prompt_tokens=5_000)

    assert absurdo.uncached_prompt_tokens == 0
    assert _FLASH_PEAK.cost_usd(absurdo) >= 0


def test_sem_preco_de_cache_o_hit_sai_no_preco_do_miss() -> None:
    """Sabotagem: tratar `input_cache_hit_per_1m=None` como zero.

    O `deepseek-v4-pro` não publica preço de cache separado. `None` tem
    de significar "cobra como miss" (o lado seguro), não "é de graça" —
    que subestimaria a conta.
    """
    preco = Price(input_cache_miss_per_1m=1.32, output_per_1m=3.96)
    usage = Usage(1_000_000, 0, cached_prompt_tokens=900_000)

    assert preco.cost_usd(usage) == pytest.approx(1.32)


# ---------------------------------------------------------------
# Horário de pico
# ---------------------------------------------------------------
@pytest.mark.parametrize(
    "quando,espera_pico",
    [
        (_em(1, 0), True),  # abre a primeira janela
        (_em(0, 59), False),  # um minuto antes
        (_em(3, 59), True),  # último minuto dentro
        (_em(4, 0), False),  # fecha: fronteira é exclusiva
        (_em(6, 0), True),  # abre a segunda
        (_em(9, 59), True),
        (_em(10, 0), False),  # fecha
        (_em(5, 0), False),  # entre as duas janelas
        (_em(23, 0), False),  # a hora em que o Age é usado
        (_em(2, 0, dia=24), False),  # sábado: fora de pico inteiro
        (_em(8, 0, dia=25), False),  # domingo
    ],
)
def test_janela_de_pico(quando: datetime, espera_pico: bool) -> None:
    """Sabotagem que derruba:

    - `<=` no fim da janela → cai em 04:00 e 10:00;
    - remover a checagem de fim de semana → cai em sábado e domingo;
    - comparar só `hour`, ignorando o minuto → cai em 00:59 e 09:59;
    - usar o fuso local em vez de converter para UTC → cai em quase
      todos, porque a máquina de desenvolvimento está em UTC-3.
    """
    tabela = _PrecoPorHorario(peak=_FLASH_PEAK, off_peak=_FLASH_OFF)
    esperado = _FLASH_PEAK if espera_pico else _FLASH_OFF

    assert tabela.price_at(quando) is esperado


def test_fora_de_pico_custa_metade() -> None:
    """Sabotagem: usar uma tabela só (o código de antes).

    É o defeito A em uma linha: a mesma chamada, duas horas, dois
    preços.
    """
    usage = Usage(1_000_000, 1_000_000)
    tabela = _PrecoPorHorario(peak=_FLASH_PEAK, off_peak=_FLASH_OFF)

    no_pico = tabela.price_at(_em(2)).cost_usd(usage)
    fora = tabela.price_at(_em(23)).cost_usd(usage)

    assert fora == pytest.approx(no_pico / 2)


def test_instante_em_outro_fuso_e_convertido() -> None:
    """Sabotagem: comparar a hora sem `astimezone(UTC)`.

    02:00 UTC é 23:00 do dia anterior em São Paulo. Quem passa um
    instante com fuso tem de receber o preço da hora UTC dele, não do
    número que aparece no relógio local.
    """
    from zoneinfo import ZoneInfo

    tabela = _PrecoPorHorario(peak=_FLASH_PEAK, off_peak=_FLASH_OFF)
    # 23:00 em São Paulo = 02:00 UTC do dia seguinte = pico.
    em_sp = datetime(2026, 10, 19, 23, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))

    assert tabela.price_at(em_sp) is _FLASH_PEAK


# ---------------------------------------------------------------
# A ponte com ModelCapabilities
# ---------------------------------------------------------------
def test_preco_de_pico_bate_com_o_teto_declarado() -> None:
    """Sabotagem: mudar a tabela e esquecer `cost_per_1m_input`.

    `cost_per_1m_*` continua existindo como teto da cota, e isso cria
    redundância com `pricing`. Redundância é onde a próxima divergência
    nasce, então ela é verificada em vez de confiada.
    """
    for caps in (_FLASH, _PRO):
        assert caps.pricing is not None
        no_pico = caps.pricing.price_at(_em(2))
        assert caps.cost_per_1m_input == no_pico.input_cache_miss_per_1m
        assert caps.cost_per_1m_output == no_pico.output_per_1m


def test_modelo_sem_preco_continua_none() -> None:
    """Sabotagem: um `or 0.0` em qualquer ponto do caminho.

    Modelo local não tem preço, e isso é `None` — não zero fingido. A
    distinção percorre a feature inteira: `None` desenha "custo
    desconhecido", `0.0` desenha "foi de graça".
    """
    local = ModelCapabilities(
        supports_tools=False,
        supports_parallel_tool_calls=False,
        supports_streaming=True,
        supports_system_role=True,
        supports_json_mode=False,
        context_window=8_192,
        max_output_tokens=2_048,
    )

    assert local.estimate_cost_usd(Usage(1_000, 1_000)) is None


def test_estimate_usa_o_instante_passado() -> None:
    """Sabotagem: ignorar `at` e usar sempre `now()`.

    Sem isso, o custo de uma chamada feita às 02:00 seria precificado
    pela hora em que o relatório foi gerado.
    """
    usage = Usage(1_000_000, 0)

    assert _FLASH.estimate_cost_usd(usage, at=_em(2)) == pytest.approx(0.30)
    assert _FLASH.estimate_cost_usd(usage, at=_em(23)) == pytest.approx(0.15)


def test_assinatura_de_um_argumento_continua_valida() -> None:
    """Sabotagem: tornar `at` obrigatório.

    O teste de contrato dos provedores chama
    `estimate_cost_usd(Usage(...))` com um argumento só. Quebrar isso
    quebraria a suíte de contrato inteira, que é justamente a que
    garante que um provedor novo se comporta.
    """
    assert _FLASH.estimate_cost_usd(Usage(1_000_000, 0)) is not None


def test_usage_posicional_continua_valido() -> None:
    """Sabotagem: pôr `cached_prompt_tokens` antes dos originais.

    `test_llm_contract.py` constrói `Usage(1_000_000, 1_000_000)`
    posicionalmente. Campo novo tem de vir depois e com default.
    """
    usage = Usage(1_000_000, 1_000_000)

    assert usage.prompt_tokens == 1_000_000
    assert usage.completion_tokens == 1_000_000
    assert usage.cached_prompt_tokens is None


def test_flat_pricing_ignora_a_hora() -> None:
    """Provedor sem variação horária responde o mesmo sempre.

    Sabotagem: fazer `FlatPricing` consultar a hora. Ela existe
    justamente para que "não varia" seja dizível sem o chamador saber
    quem varia.
    """
    plano = FlatPricing(price=Price(input_cache_miss_per_1m=2.50, output_per_1m=10.0))

    assert plano.price_at(_em(2)) is plano.price_at(_em(23))
    assert plano.price_at(TERCA).input_cache_miss_per_1m == 2.50
