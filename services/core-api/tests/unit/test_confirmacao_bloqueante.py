"""A confirmação de conflito bloqueante, que era aceita e jogada fora.

ARQUITETURA.md §5.7, §5.8.

`ApplyIn.confirm_blocking` existia na rota desde sempre. Um grep no
core-api inteiro devolvia UMA ocorrência: a própria declaração do
campo. A rota não repassava, `apply_plan` não tinha o parâmetro, e o
executor recusava incondicionalmente qualquer item bloqueante.

O efeito era a pior forma de falha, porque parecia sucesso:

    marca a caixa -> botão habilita -> POST confirm_blocking: true
      -> HTTP 200 {applied_count: 0, status: "failed"}
      -> o cartão some da tela
      -> nenhuma mensagem de erro, porque um FAILED não preenche
         `manual_fix_needed`, que era a única coisa que a tela olhava
      -> nada agendado

O relato do usuário foi "tu nunca confirma porra nenhuma", e ele estava
descrevendo exatamente esta sequência.

Estes testes existem porque NENHUM teste exercia o parâmetro. O que
havia (`test_blocking_conflict_prevents_apply`) chamava `apply_plan`
direto e afirmava que item bloqueante é recusado, o que continua
verdade e continua passando: ele trava a metade segura e é cego para a
metade que faltava.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.adapters.calendar.in_memory import InMemoryCalendar
from app.domain.plan_diff import (
    ConflictKind,
    PlanBuilder,
    PlanStatus,
    validate_plan,
)
from app.domain.plan_executor import apply_plan
from app.ports.calendar import BusyInterval, Event, Flexibility

SP = ZoneInfo("America/Sao_Paulo")
FUTURE = 2027


def ev(
    hour: int,
    *,
    title: str = "Estudo",
    day: int = 15,
    flex: Flexibility = Flexibility.FIXED,
    eid: str | None = None,
    duration_h: int = 1,
) -> Event:
    start = datetime(FUTURE, 3, day, hour, tzinfo=SP)
    return Event(
        id=eid,
        calendar_id="primary",
        title=title,
        start=start,
        end=start + timedelta(hours=duration_h),
        flexibility=flex,
    )


def plano_com_choque():
    """Um plano cujo único item choca com uma prova marcada como FIXED.

    É o caminho mais comum de verdade: o modelo propõe estudo e acerta
    em cima de algo que não se move.
    """
    b = PlanBuilder(uuid4(), uuid4())
    b.propose_create(ev(14, title="Estudo de Álgebra"), "bloco livre")
    prova = ev(14, title="Prova de Cálculo", flex=Flexibility.FIXED, eid="p1")
    return validate_plan(
        b.build(),
        [BusyInterval(start=prova.start, end=prova.end)],
        [prova],
    )


# ---------------------------------------------------------------
# Sem confirmação: continua recusando
# ---------------------------------------------------------------
async def test_sem_confirmacao_recusa() -> None:
    """A trava segue de pé quando ninguém confirmou nada."""
    plan = plano_com_choque()
    assert plan.items[0].is_blocking, "o cenário precisa ser bloqueante"

    result = await apply_plan(plano_com_choque(), InMemoryCalendar())

    assert result.status == PlanStatus.FAILED
    assert result.applied == []
    assert "conflito bloqueante" in result.failed[0][1]


async def test_sem_confirmacao_nao_escreve_na_agenda() -> None:
    """Recusar é não escrever, não "escrever e reverter"."""
    provider = InMemoryCalendar()
    await apply_plan(plano_com_choque(), provider)

    eventos = await provider.list_events(
        ["primary"],
        datetime(FUTURE, 3, 15, tzinfo=SP),
        datetime(FUTURE, 3, 16, tzinfo=SP),
    )
    assert eventos == []


# ---------------------------------------------------------------
# Com confirmação: aplica
# ---------------------------------------------------------------
async def test_com_confirmacao_aplica() -> None:
    """O teste de regressão do defeito.

    Sabotagem que precisa derrubá-lo: tirar `confirm_blocking` da
    assinatura de `apply_plan`, ou parar de repassá-lo na rota.
    """
    result = await apply_plan(
        plano_com_choque(),
        InMemoryCalendar(),
        confirm_blocking=True,
    )

    assert result.status == PlanStatus.APPLIED
    assert len(result.applied) == 1


async def test_com_confirmacao_o_evento_entra_mesmo() -> None:
    """Afirmar sobre a AGENDA, não só sobre o status devolvido.

    `status == APPLIED` com zero eventos criados passaria no teste
    acima e seria exatamente o tipo de mentira que este módulo existe
    para impedir.
    """
    provider = InMemoryCalendar()
    await apply_plan(plano_com_choque(), provider, confirm_blocking=True)

    eventos = await provider.list_events(
        ["primary"],
        datetime(FUTURE, 3, 15, tzinfo=SP),
        datetime(FUTURE, 3, 16, tzinfo=SP),
    )
    assert [e.title for e in eventos] == ["Estudo de Álgebra"]


async def test_confirmacao_nao_atropela_plano_sem_conflito() -> None:
    """Contrapeso: confirmar não pode ser o que faz o plano funcionar.

    Sem isto, um executor que simplesmente aplicasse tudo sempre
    passaria nos testes acima.
    """
    b = PlanBuilder(uuid4(), uuid4())
    b.propose_create(ev(9, title="Leitura"), "manhã livre")
    plan = validate_plan(b.build(), [], [])
    assert not plan.items[0].is_blocking

    result = await apply_plan(plan, InMemoryCalendar())
    assert result.status == PlanStatus.APPLIED


# ---------------------------------------------------------------
# `refused_upfront`: a recusa não encerra a proposta
# ---------------------------------------------------------------
async def test_recusa_e_marcada_como_pre_voo() -> None:
    """O sinal que impede a rota de encerrar o plano.

    `mark_applied` rodava incondicionalmente e tirava o plano de
    `proposed`, marcando `applied_at` num plano onde nada foi aplicado.
    Era o que tornava o defeito PERMANENTE: nem recarregar a página
    trazia o cartão de volta, porque a listagem de propostos já não o
    continha.
    """
    result = await apply_plan(plano_com_choque(), InMemoryCalendar())
    assert result.refused_upfront is True


async def test_aplicacao_bem_sucedida_nao_e_pre_voo() -> None:
    """Contrapeso: o campo não pode ser `True` sempre."""
    result = await apply_plan(
        plano_com_choque(),
        InMemoryCalendar(),
        confirm_blocking=True,
    )
    assert result.refused_upfront is False


# ---------------------------------------------------------------
# Folga do passado (§5.7)
# ---------------------------------------------------------------
def _plano_comecando_em(delta: timedelta):
    """Um plano cujo bloco começa a `delta` de agora."""
    agora = datetime.now(SP)
    inicio = agora + delta
    b = PlanBuilder(uuid4(), uuid4())
    b.propose_create(
        Event(
            id=None,
            calendar_id="primary",
            title="Estudo",
            start=inicio,
            end=inicio + timedelta(hours=1),
            flexibility=Flexibility.FLEXIBLE,
        ),
        "a próxima hora",
    )
    return validate_plan(b.build(), [], [], now=agora)


def _tem_conflito_de_passado(plan) -> bool:
    return ConflictKind.PAST in {c.kind for c in plan.items[0].conflicts}


def test_bloco_que_comecou_ha_tres_minutos_nao_e_passado() -> None:
    """Nao pode nascer bloqueado o pedido mais natural que existe.

    A validação roda na LEITURA, com `now = agora`, e entre propor e
    clicar passa tempo real: a promoção de rascunho para plano é
    preguiçosa e já foi medida em 98 segundos na conta de verdade,
    somada ao tempo de ler o cartão e decidir. Sem a folga, o pedido
    mais natural que existe chega bloqueado por causa do atraso do
    próprio sistema.

    O delta é NEGATIVO de propósito. A primeira versão deste teste
    usava +5 min, e um bloco no futuro nunca é passado, com folga ou
    sem: ele passava com `PAST_GRACE = 0` e não testava nada. A
    sabotagem pegou; a leitura do código, não.
    """
    assert not _tem_conflito_de_passado(_plano_comecando_em(timedelta(minutes=-3)))


def test_bloco_de_ontem_continua_sendo_passado() -> None:
    """Contrapeso: a folga é de minutos, não um desligamento da regra."""
    assert _tem_conflito_de_passado(_plano_comecando_em(timedelta(hours=-24)))


def test_a_folga_termina() -> None:
    """Meia hora atrás está fora da folga de dez minutos."""
    assert _tem_conflito_de_passado(_plano_comecando_em(timedelta(minutes=-30)))
