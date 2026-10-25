"""Persistência e promoção de planos.

ARQUITETURA.md §5.7 e §2.3.2. Duas responsabilidades:

1. **Promover rascunhos** de `ai.plan_drafts` para `core.plans`,
   resolvendo os eventos referenciados e validando conflitos. A
   validação roda aqui, na leitura — não na proposta — então um
   conflito que surgiu nesse intervalo aparece.

2. **Carregar e marcar** planos para as rotas de aprovação.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.plan_diff import (
    DEFAULT_TTL,
    Conflict,
    ConflictKind,
    DiffItem,
    ItemStatus,
    Op,
    Plan,
    PlanStatus,
    Target,
    validate_plan,
)
from app.ports.calendar import Event, Flexibility
from app.ports.tasks import Priority, Task, TaskStatus
from app.services.calendar_access import load_policy, read_busy, read_events

log = logging.getLogger("core-api.plan_store")


# ---------------------------------------------------------------
# Promoção de rascunho
# ---------------------------------------------------------------
async def promote_drafts(db: AsyncSession, user_id: UUID) -> list[UUID]:
    """Converte rascunhos do worker em planos validados.

    Idempotente: rascunho já promovido é ignorado.

    `FOR UPDATE SKIP LOCKED` não é otimização, é correção.

    Desde que `GET /conversations/{id}/messages` também promove, DUAS
    rotas chamam esta função, e no fim de um turno a tela chama as duas
    quase juntas. Medido na conta do usuário: `/plans` às 23:57:47.938
    e `/messages` às 23:57:47.944 — **6 milissegundos** de diferença.

    As duas transações leram o mesmo rascunho com `promoted = false`,
    as duas promoveram, e o usuário ficou com DOIS planos idênticos de
    7 itens: 12 cartões onde deviam haver 6. Foi o que ele relatou como
    "ele marcou coisa duplicada, fez uma lambança".

    `SKIP LOCKED` e não só `FOR UPDATE`: quem perde a corrida pula o
    rascunho em vez de esperar e promover depois. Esperar daria o mesmo
    resultado duplicado, só mais devagar — o `UPDATE` do vencedor ainda
    não estaria visível para a outra transação.

    Meu teste de idempotência não pegou isto porque rodava as duas
    promoções EM SEQUÊNCIA, e em sequência o código antigo acertava.
    """
    rows = await db.execute(
        text(
            """
            SELECT id, turn_id, conversation_id, summary, items
            FROM ai.plan_drafts
            WHERE user_id = :uid AND promoted = false
            ORDER BY created_at
            FOR UPDATE SKIP LOCKED
            """
        ),
        {"uid": str(user_id)},
    )
    drafts = rows.all()
    if not drafts:
        return []

    created: list[UUID] = []
    for draft_id, _turn_id, conversation_id, summary, items in drafts:
        try:
            plan_id = await _promote_one(
                db, user_id, conversation_id, summary, items or []
            )
        except Exception as exc:  # noqa: BLE001
            # Rascunho ruim não deve impedir os outros nem travar a
            # leitura da conversa. Marca como promovido para não ficar
            # tentando eternamente.
            log.warning("rascunho %s não pôde ser promovido: %s", draft_id, exc)
            await db.execute(
                text(
                    "UPDATE ai.plan_drafts SET promoted = true "
                    "WHERE id = :did AND user_id = :uid"
                ),
                {"did": str(draft_id), "uid": str(user_id)},
            )
            continue

        await db.execute(
            text(
                """
                UPDATE ai.plan_drafts
                SET promoted = true, promoted_plan_id = :pid
                WHERE id = :did AND user_id = :uid
                """
            ),
            {"did": str(draft_id), "pid": str(plan_id), "uid": str(user_id)},
        )
        created.append(plan_id)

    return created


async def build_plan_from_items(
    db: AsyncSession,
    user_id: UUID,
    *,
    summary: str,
    raw_items: list[dict],
    conversation_id: UUID | None = None,
) -> UUID:
    """Cria um plano a partir de itens montados pela UI, não pelo LLM.

    Existe para os botões que §8.1 descreve como atalhos do chat — o
    "reservar tempo" da lista de tarefas é o primeiro deles: "chama
    `POST /api/tasks/{id}/schedule`, que gera o par ligado — mesmo
    resultado de pedir no chat, sem precisar escrever".

    ## Por que não pela fila, como o worker faz

    O caminho do worker é inserir em `ai.plan_drafts` e deixar o
    core-api promover. Não serve aqui, e a razão é a fronteira de
    §2.3.2: o `core_api` tem **SELECT** em `ai`, e um `UPDATE` cirúrgico
    só nas colunas `promoted` e `promoted_plan_id` (migration 0004 do
    ai-worker). Ele não pode inserir rascunho, e alargar esse GRANT para
    acomodar um botão de UI trocaria uma garantia estrutural por
    conveniência — o schema `ai` deixaria de ser escrito só por quem o
    possui.

    Passar pelo worker também seria pior em produto: a reserva de tempo
    aqui não precisa de modelo nenhum. O horário vem do `find_free_slots`
    (determinístico, §5.3) ou do próprio usuário, e enfileirar um job de
    LLM para montar um item que a UI já conhece gastaria token e
    adicionaria a latência da fila a um clique.

    ## O que ela NÃO faz

    Não aplica. Devolve o id de um plano `proposed`, que passa pela
    mesma revisão granular de §5.8 — inclusive a detecção de conflito,
    porque `validate_plan` roda igual. Um atalho que escrevesse direto
    na agenda seria um segundo caminho de escrita sem `PlanDiff`, sem
    motivo registrado e fora do `plan_undo_log`.
    """
    return await _promote_one(db, user_id, conversation_id, summary, raw_items)


async def _promote_one(
    db: AsyncSession,
    user_id: UUID,
    conversation_id: UUID | None,
    summary: str | None,
    raw_items: list[dict],
) -> UUID:
    policy = await load_policy(db, user_id)

    # Rascunhos de ROTINA são tratados antes: cada um vira a série em
    # `core.routines` mais um item de plano por ocorrência da janela.
    #
    # Por que a série é criada aqui e não na aplicação: ela não é uma
    # mudança de agenda, é a declaração do compromisso. O que passa por
    # aprovação são os EVENTOS — e sem a série existindo, as ocorrências
    # não teriam a que pertencer, e "fui à academia essa semana?" ficaria
    # sem resposta porque não haveria aderência para consultar.
    #
    # A série nasce pausada em `routines.paused_at`? Não: nasce ativa,
    # mas sem nenhum evento na agenda até a aprovação. É o estado
    # honesto — o compromisso existe, o tempo ainda não foi reservado.
    expanded_items: list[dict] = []
    for raw in raw_items:
        if raw.get("target") != "routine":
            expanded_items.append(raw)
            continue
        try:
            expanded_items.extend(
                await _promote_routine(db, user_id, raw, policy.timezone)
            )
        except (ValueError, KeyError) as exc:
            log.info("rascunho de rotina descartado: %s", exc)

    raw_items = expanded_items

    # Calendario de escrita, resolvido AQUI e nao no executor: o
    # executor recebe o provedor por porta (secao 3.2) e nao deve saber
    # o conceito de "calendario default". Resolver aqui tambem deixa o
    # item do plano completo quando o usuario o revisa.
    write_calendar_id = await _resolve_write_calendar(db, user_id)
    # Mesma resolução para a lista: o executor recebe a porta e não
    # deve conhecer o conceito de "lista default" (§3.3).
    write_list_id = await _resolve_write_list(db, user_id, raw_items)
    existing_tasks = await _load_tasks_by_id(db, user_id, raw_items)
    # Blocos que JÁ ESTÃO na agenda e que alguma tarefa passa a cobrir
    # (§5.7.1). Resolvidos por ID, e não pela janela de `read_events`
    # abaixo: a janela é derivada dos horários DO PLANO, então um plano
    # só de tarefas tem janela "de ontem até daqui a 7 dias" e perderia
    # justamente o bloco de duas semanas à frente.
    blocos_resolvidos = await _resolver_blocos(db, user_id, raw_items)
    items: list[DiffItem] = []
    # Mapa do id do rascunho para o id do DiffItem, para refazer os
    # vínculos do par ligado (§5.7.1) depois de todos os itens
    # existirem — um item pode referenciar outro que vem depois.
    draft_to_item: dict[str, UUID] = {}

    # Janela que cobre todos os itens, para carregar o contexto de
    # validação de uma vez.
    moments = []
    for raw in raw_items:
        payload = raw.get("payload", {})
        for key in ("start", "new_start"):
            if payload.get(key):
                try:
                    moments.append(datetime.fromisoformat(payload[key]))
                except ValueError:
                    pass

    now = datetime.now(UTC)
    # A janela cobre os EVENTOS DO PLANO, não de hoje até eles. Incluir
    # `now` no mínimo parecia defensivo e quebrava qualquer plano para
    # daqui a mais de 90 dias: a janela virava "hoje até o evento" e
    # estourava o limite de §7.0.
    if moments:
        window_start = min(moments) - timedelta(days=1)
        window_end = max(moments) + timedelta(days=1)
    else:
        # Sem horário em nenhum item: janela curta em torno de agora.
        window_start = now - timedelta(days=1)
        window_end = now + timedelta(days=7)

    existing = await read_events(db, user_id, window_start, window_end)
    by_id = {e.id: e for e in existing if e.id}
    busy = await read_busy(db, user_id, window_start, window_end)

    # A janela sai dos horários DO PLANO, e um item que só REFERENCIA um
    # evento (MOVE, DELETE) não traz horário nenhum — então não empurra
    # a janela. Sem nenhum horário em item nenhum, a janela vira "ontem
    # até daqui a 7 dias", e um evento de novembro cai fora dela.
    #
    # Medido na conta real em 25/10/2026: três ids recusados com "não
    # existe na agenda" eram os filmes da Pixar, vivos em
    # `core.local_events`. O plano nasceu vazio e a tela ofereceu
    # "Aplicar os 0 itens".
    #
    # `_resolver_blocos` nunca teve esse problema porque resolve POR ID,
    # e a docstring de `task_store.resolve_events` já dizia por quê:
    # obrigar quem chama a reconstruir a janela certa "falharia em
    # silêncio justamente para o bloco que está um dia fora dela".
    by_id.update(await _eventos_referenciados(db, user_id, raw_items, by_id))

    for index, raw in enumerate(raw_items):
        op = raw.get("op", "")
        reason = (raw.get("reason") or "").strip()
        payload = raw.get("payload", {})

        if not reason:
            # `reason` é obrigatório por contrato (§5.6). Rascunho sem
            # ele é descartado em vez de ganhar motivo inventado.
            log.info("item %s descartado: sem reason", index)
            continue

        try:
            item = _build_item(
                op=op,
                target=raw.get("target", "event"),
                reason=reason,
                payload=payload,
                sequence=index,
                existing=by_id,
                existing_tasks=existing_tasks,
                timezone=policy.timezone,
                write_calendar_id=write_calendar_id,
                write_list_id=write_list_id,
                blocos_resolvidos=blocos_resolvidos,
            )
        except (ValueError, KeyError) as exc:
            log.info("item %s descartado: %s", index, exc)
            continue

        if item is not None:
            items.append(item)
            if raw.get("id"):
                draft_to_item[str(raw["id"])] = item.id

    # Vínculos, agora que todos os itens têm id definitivo. Um item cujo
    # par foi descartado perde o vínculo em vez de apontar para o vazio:
    # meio par é pior que nenhum, e `linked_group` trataria um id
    # inexistente como grupo de um só.
    items = _relink(items, raw_items, draft_to_item)

    # Plano sem NENHUM item não vira cartão.
    #
    # Medido na conta real: o Age respondeu "Preparei os 4 vínculos,
    # todos aceitos", com os quatro numa tabela, e a tela mostrou
    # "0 itens · 0 marcados" com o botão "Aplicar os 0 itens". Os quatro
    # haviam caído um a um, cada um com seu `log.info`, e o plano nasceu
    # assim mesmo.
    #
    # Um cartão que não pode aplicar nada é pior que erro nenhum: ele
    # confirma por escrito o que não aconteceu. Levantar aqui faz o
    # chamador registrar `log.warning` — visível — e marcar o rascunho
    # como promovido, sem ficar tentando eternamente.
    #
    # Isto NÃO é o caso de ALGUNS itens caírem: esse continua gerando
    # plano com os que sobraram, porque aprovação é granular (§5.8).
    if raw_items and not items:
        raise ValueError(
            f"todos os {len(raw_items)} itens foram descartados; "
            "não há plano para propor"
        )

    plan = Plan(
        id=uuid4(),
        user_id=user_id,
        conversation_id=conversation_id,
        items=items,
        summary=summary or "",
        expires_at=now + DEFAULT_TTL,
    )
    plan = validate_plan(
        plan,
        busy,
        [e for e in existing if e.flexibility == Flexibility.FIXED],
        now=now,
        max_hours_per_day=policy.max_hours_per_day,
    )

    await _persist(db, plan)
    return plan.id


async def _resolve_write_calendar(db: AsyncSession, user_id: UUID) -> str:
    """Onde o Age escreve por padrao.

    Respeita `default_write_calendar_id` das preferencias (modo hibrido,
    secao 3.2.1); senao usa o calendario nativo, criando-o se preciso.
    """
    from app.adapters.calendar.local import LocalCalendar

    preferred = (
        await db.execute(
            text(
                "SELECT default_write_calendar_id "
                "FROM core.user_preferences WHERE user_id = :uid"
            ),
            {"uid": str(user_id)},
        )
    ).scalar_one_or_none()

    if preferred:
        return preferred

    local = LocalCalendar(db=db, user_id=user_id)
    default = await local.ensure_default_calendar()
    return default.id


def _build_item(
    *,
    op: str,
    target: str,
    reason: str,
    payload: dict,
    sequence: int,
    existing: dict[str, Event],
    existing_tasks: dict[str, Task],
    timezone: str,
    write_calendar_id: str,
    write_list_id: str,
    blocos_resolvidos: dict[str, tuple[str, datetime]] | None = None,
) -> DiffItem | None:
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(timezone)

    def parse(value: str) -> datetime:
        moment = datetime.fromisoformat(value)
        # Modelo às vezes manda sem fuso; assume o do usuário em vez de
        # recusar, porque recusar perderia a proposta inteira.
        return moment if moment.tzinfo else moment.replace(tzinfo=tz)

    if target == "task":
        return _build_task_item(
            op=op,
            reason=reason,
            payload=payload,
            sequence=sequence,
            existing_tasks=existing_tasks,
            write_list_id=write_list_id,
            parse=parse,
            blocos_resolvidos=blocos_resolvidos or {},
        )

    match op:
        case "CREATE":
            start = parse(payload["start"])
            if payload.get("duration_min"):
                end = start + timedelta(minutes=int(payload["duration_min"]))
            else:
                end = parse(payload["end"])

            return DiffItem(
                id=uuid4(),
                target=Target.EVENT,
                op=Op.CREATE,
                reason=reason,
                sequence=sequence,
                after=Event(
                    id=None,
                    calendar_id=write_calendar_id,
                    title=payload["title"],
                    start=start,
                    end=end,
                    description=payload.get("description"),
                    recurrence=payload.get("rrule"),
                    flexibility=Flexibility(
                        payload.get("flexibility", "flexible")
                    ),
                    source="age",
                    # Ocorrência de série: o evento sabe de qual rotina
                    # veio, e é isso que permite mover uma ocorrência
                    # sem perder a aderência dela.
                    routine_id=payload.get("routine_id"),
                ),
            )

        case "MOVE":
            before = existing.get(payload["event_id"])
            if before is None:
                # O modelo pode alucinar um evento. Descartar é melhor
                # que propor mudança em algo que não existe (§9.5).
                raise ValueError(
                    f"evento {payload['event_id']} não existe na agenda"
                )

            from dataclasses import replace

            return DiffItem(
                id=uuid4(),
                target=Target.EVENT,
                op=Op.MOVE,
                reason=reason,
                sequence=sequence,
                target_ref=before.id,
                before=before,
                after=replace(
                    before,
                    start=parse(payload["new_start"]),
                    end=parse(payload["new_end"]),
                ),
            )

        case "DELETE":
            before = existing.get(payload["event_id"])
            if before is None:
                raise ValueError(
                    f"evento {payload['event_id']} não existe na agenda"
                )
            return DiffItem(
                id=uuid4(),
                target=Target.EVENT,
                op=Op.DELETE,
                reason=reason,
                sequence=sequence,
                target_ref=before.id,
                before=before,
            )

        case _:
            raise ValueError(f"operação não suportada: {op}")


async def _promote_routine(
    db: AsyncSession, user_id: UUID, raw: dict, timezone_name: str
) -> list[dict]:
    """Cria a série e devolve um rascunho de evento por ocorrência.

    A janela é de 28 dias por padrão — o mesmo horizonte que §5.9.4 usa
    para contar conflitos ("só 2 conflitos no mês"). Materializar um ano
    de uma vez encheria a agenda de eventos que o usuário ainda não
    decidiu manter, e cada rematerialização é idempotente, então não há
    perda em fazer por janelas.
    """
    from datetime import timedelta as _td

    from app.services.routine_store import create_routine, materialize

    payload = raw.get("payload", {})
    reason = (raw.get("reason") or "").strip()
    if not reason:
        raise ValueError("rotina sem reason")

    title = payload.get("title")
    rrule = payload.get("rrule")
    first_start = payload.get("first_start")
    duration_min = payload.get("duration_min")
    if not (title and rrule and first_start and duration_min):
        raise ValueError("rotina incompleta")

    from zoneinfo import ZoneInfo

    tz = ZoneInfo(timezone_name)
    started = datetime.fromisoformat(first_start)
    if started.tzinfo is None:
        started = started.replace(tzinfo=tz)

    routine_id = await create_routine(
        db,
        user_id,
        title=title,
        rrule=rrule,
        duration_min=int(duration_min),
        started_at=started,
        flexibility=payload.get("flexibility", "flexible"),
        target_per_week=payload.get("target_per_week"),
    )

    routine = await _load_routine_for_promotion(db, user_id, routine_id)
    window_end = started + _td(days=28)
    await materialize(
        db, user_id, routine, window_start=started, window_end=window_end
    )

    from app.domain.routines import expand

    occurrences = expand(
        routine, window_start=started, window_end=window_end
    )

    # Um item de plano por ocorrência. O usuário aprova a série inteira
    # ou escolhe quais ocorrências quer — aprovação granular de §8.1
    # funciona aqui sem nada especial, porque cada ocorrência é um item.
    out: list[dict] = []
    for index, occurrence in enumerate(occurrences):
        out.append(
            {
                "id": f"{raw.get('id')}-{index}",
                "op": "CREATE",
                "target": "event",
                "reason": (
                    reason
                    if index == 0
                    else f"{reason} (ocorrência {index + 1} da série)"
                ),
                "payload": {
                    "title": title,
                    "start": occurrence.start.isoformat(),
                    "end": occurrence.end.isoformat(),
                    "flexibility": payload.get("flexibility", "flexible"),
                    "routine_id": str(routine_id),
                },
                "sequence": index,
            }
        )
    return out


async def _load_routine_for_promotion(db, user_id, routine_id):
    from app.services.routine_store import load_routine

    routine = await load_routine(db, user_id, routine_id)
    if routine is None:  # pragma: no cover — acabou de ser criada
        raise ValueError("rotina criada mas não encontrada")
    return routine


async def _resolve_write_list(
    db: AsyncSession, user_id: UUID, raw_items: list[dict]
) -> str:
    """Onde o Age escreve tarefa por padrão (§3.3.3).

    Só cria a lista nativa se o rascunho tiver algum item de tarefa: um
    plano que só mexe na agenda não deve ter como efeito colateral criar
    uma lista que o usuário nunca pediu.
    """
    if not any(raw.get("target") == "task" for raw in raw_items):
        return ""

    preferred = (
        await db.execute(
            text(
                "SELECT default_write_task_list_id "
                "FROM core.user_preferences WHERE user_id = :uid"
            ),
            {"uid": str(user_id)},
        )
    ).scalar_one_or_none()
    if preferred:
        return preferred

    from app.adapters.tasks.local import LocalTasks

    default = await LocalTasks(db=db, user_id=user_id).ensure_default_list()
    return default.id


async def _load_tasks_by_id(
    db: AsyncSession, user_id: UUID, raw_items: list[dict]
) -> dict[str, Task]:
    """Carrega só as tarefas citadas nos rascunhos.

    Carregar a lista inteira seria mais simples e errado: quem usa o
    produto de verdade tem dezenas de tarefas, e o plano mexe em três.
    """
    wanted = {
        str(raw.get("payload", {}).get("task_id"))
        for raw in raw_items
        if raw.get("target") == "task"
        and raw.get("payload", {}).get("task_id")
    }
    for raw in raw_items:
        if raw.get("target") == "task":
            for tid in raw.get("payload", {}).get("ordered_ids") or []:
                wanted.add(str(tid))

    if not wanted:
        return {}

    from app.services.task_store import read_tasks

    # `include_done=True`: uma tarefa concluída entre a proposta e a
    # promoção tem de ser ENCONTRADA, para o conflito aparecer na
    # revalidação. Se ela não viesse, o item seria descartado como
    # "não existe" e o usuário procuraria o que não se perdeu.
    everything = await read_tasks(db, user_id, include_done=True)
    return {
        e.task.id: e.task
        for e in everything
        if e.task.id and e.task.id in wanted
    }


async def _eventos_referenciados(
    db: AsyncSession,
    user_id: UUID,
    raw_items: list[dict],
    ja_tem: dict[str, Event],
) -> dict[str, Event]:
    """Eventos citados por id que a janela do plano não alcançou.

    Complementa `by_id`, não o substitui: a janela continua sendo o
    caminho barato do caso comum (plano com horário próprio), e isto
    cobre só o que ela deixa de fora.

    Busca apenas o que FALTA — um plano cujos eventos já estão na
    janela não faz consulta nenhuma a mais.
    """
    faltando: list[str] = []
    for raw in raw_items:
        if raw.get("target") not in (None, "event"):
            continue
        ref = raw.get("payload", {}).get("event_id")
        if ref and str(ref) not in ja_tem and str(ref) not in faltando:
            faltando.append(str(ref))

    if not faltando:
        return {}

    from app.services.task_store import resolve_events

    achados: dict[str, Event] = {}
    for achado in await resolve_events(db, user_id, faltando):
        # Só evento local: `events_shadow` espelha calendário externo, e
        # MOVE/DELETE nele não é nosso para propor (§6.3).
        if not achado.found or achado.kind != "local_events":
            continue
        if achado.start is None or achado.end is None:
            continue
        achados[achado.ref_id] = Event(
            id=achado.ref_id,
            calendar_id="",
            title=achado.title,
            start=achado.start,
            end=achado.end,
        )
    return achados


async def _resolver_blocos(
    db: AsyncSession, user_id: UUID, raw_items: list[dict]
) -> dict[str, tuple[str, datetime]]:
    """Resolve os blocos existentes citados nos rascunhos (§5.7.1).

    Devolve `{ref_id: (kind, fim)}`. O `kind` é o que diz em qual das
    duas tabelas de evento o vínculo vai apontar; o `fim` serve para
    recusar bloco que já terminou, mais abaixo.

    Só os NÃO encontrados ficam de fora, e o descarte é registrado onde
    acontece. Nada aqui levanta: a tarefa vale sem o vínculo.
    """
    desejados: list[str] = []
    for raw in raw_items:
        if raw.get("target") != "task":
            continue
        for ref in raw.get("payload", {}).get("event_refs") or []:
            if str(ref) not in desejados:
                desejados.append(str(ref))

    if not desejados:
        return {}

    from app.services.task_store import resolve_events

    agora = datetime.now(UTC)
    out: dict[str, tuple[str, datetime]] = {}
    for achado in await resolve_events(db, user_id, desejados):
        if not achado.found or achado.kind is None or achado.end is None:
            continue
        # Bloco encerrado não é tempo reservado: contaria minutos que
        # não existem mais e faria o Age dizer "3h reservadas" para três
        # horas que já passaram. Mais restrito que `PAST_GRACE` de
        # propósito, porque aqui nada está sendo agendado.
        if achado.end <= agora:
            log.info(
                "vínculo descartado: bloco %s terminou em %s",
                achado.ref_id,
                achado.end,
            )
            continue
        out[achado.ref_id] = (achado.kind, achado.end)
    return out


def _relink(
    items: list[DiffItem],
    raw_items: list[dict],
    draft_to_item: dict[str, UUID],
) -> list[DiffItem]:
    """Reconstrói os vínculos do par ligado (§5.7.1)."""
    from dataclasses import replace as _replace

    links_by_item: dict[UUID, list[UUID]] = {}
    for raw in raw_items:
        source = draft_to_item.get(str(raw.get("id")))
        if source is None:
            continue
        for linked_draft_id in raw.get("links") or []:
            target_item = draft_to_item.get(str(linked_draft_id))
            if target_item is not None:
                links_by_item.setdefault(source, []).append(target_item)

    if not links_by_item:
        return items

    return [
        _replace(item, links=tuple(links_by_item[item.id]))
        if item.id in links_by_item
        else item
        for item in items
    ]


def _build_task_item(
    *,
    op: str,
    reason: str,
    payload: dict,
    sequence: int,
    existing_tasks: dict[str, Task],
    write_list_id: str,
    parse,
    blocos_resolvidos: dict[str, tuple[str, datetime]] | None = None,
) -> DiffItem | None:
    """Monta um item de tarefa a partir do rascunho (§5.7)."""
    from dataclasses import replace as _replace

    def refs_de_blocos() -> tuple[tuple[str, str], ...]:
        """Os blocos existentes que esta tarefa passa a cobrir (§5.7.1).

        Revalidado AQUI, e não só na ferramenta que empilhou: entre
        propor e aprovar o usuário pode ter apagado o bloco, e um
        vínculo para evento que não existe mais faria a lista dizer
        "tempo reservado" apontando para o vazio.

        Bloco que não resolve é DESCARTADO, não erro: perder o vínculo
        é degradação (a tarefa nasce sem horário, visível na tela);
        levantar aqui mataria a tarefa inteira junto, que é pior e não
        é o que o usuário pediu.
        """
        resolvidos = blocos_resolvidos or {}
        out: list[tuple[str, str]] = []
        for bruto in payload.get("event_refs") or []:
            ref = str(bruto)
            achado = resolvidos.get(ref)
            if achado is None:
                log.info("vínculo descartado: bloco %s não existe mais", ref)
                continue
            kind, _fim = achado
            if (kind, ref) not in out:
                out.append((kind, ref))
        return tuple(out)

    def due_fields() -> dict:
        out: dict = {}
        if payload.get("due_at"):
            moment = parse(payload["due_at"])
            out["due_at"] = moment
            out["due_date"] = moment.date()
        elif payload.get("due_date"):
            out["due_date"] = date.fromisoformat(payload["due_date"])
            out["due_at"] = None
        return out

    match op:
        case "CREATE":
            if not payload.get("title"):
                raise ValueError("tarefa sem título")
            estimated = payload.get("estimated_min")
            return DiffItem(
                id=uuid4(),
                target=Target.TASK,
                op=Op.CREATE,
                reason=reason,
                sequence=sequence,
                after=Task(
                    id=None,
                    list_id=payload.get("list_id") or write_list_id,
                    title=payload["title"],
                    notes=payload.get("notes"),
                    priority=(
                        Priority(payload["priority"])
                        if payload.get("priority")
                        else None
                    ),
                    estimated=(
                        timedelta(minutes=int(estimated))
                        if estimated
                        else None
                    ),
                    # O tema que agrupa a tarefa com as irmãs vai aqui,
                    # como primeira etiqueta. `labels` ja atravessa todo
                    # o resto do caminho — coluna, adaptador, capacidade
                    # do provedor, `_task_json` e `TaskOut` — e faltava
                    # so neste ponto, que e justamente onde a tarefa
                    # nasce por proposta. Sem esta linha o tema morre
                    # entre a conversa e o banco, calado.
                    labels=tuple(payload.get("labels") or ()),
                    source="age",
                    **due_fields(),
                ),
                event_refs=refs_de_blocos(),
            )

        case "UPDATE" | "MOVE":
            before = existing_tasks.get(str(payload.get("task_id")))
            if before is None:
                # O modelo pode alucinar uma tarefa. Descartar é melhor
                # que propor mudança em algo que não existe (§9.5).
                raise ValueError(
                    f"tarefa {payload.get('task_id')} não existe na lista"
                )

            changes: dict = due_fields()
            if payload.get("title"):
                changes["title"] = payload["title"]
            if payload.get("priority"):
                changes["priority"] = Priority(payload["priority"])
            if payload.get("estimated_min"):
                changes["estimated"] = timedelta(
                    minutes=int(payload["estimated_min"])
                )
            if payload.get("list_id"):
                changes["list_id"] = payload["list_id"]

            if not changes:
                raise ValueError("UPDATE de tarefa sem nenhuma mudança")

            # `MOVE` em tarefa é mudança de PRAZO ou de LISTA; qualquer
            # outro campo é `UPDATE`. A distinção existe para a UI dar
            # destaque diferente — repactuar prazo merece, mudar nota
            # não.
            touches_schedule = bool(
                {"due_at", "due_date", "list_id"} & set(changes)
            )
            return DiffItem(
                id=uuid4(),
                target=Target.TASK,
                op=Op.MOVE if touches_schedule else Op.UPDATE,
                reason=reason,
                sequence=sequence,
                target_ref=before.id,
                before=before,
                after=_replace(before, **changes),
            )

        case "COMPLETE":
            before = existing_tasks.get(str(payload.get("task_id")))
            if before is None:
                raise ValueError(
                    f"tarefa {payload.get('task_id')} não existe na lista"
                )
            if before.is_done:
                # Já concluída: propor de novo seria um cartão sem
                # efeito, que só gasta a atenção de quem revisa.
                raise ValueError(f"'{before.title}' já está concluída")
            return DiffItem(
                id=uuid4(),
                target=Target.TASK,
                op=Op.COMPLETE,
                reason=reason,
                sequence=sequence,
                target_ref=before.id,
                before=before,
            )

        case "DELETE":
            before = existing_tasks.get(str(payload.get("task_id")))
            if before is None:
                raise ValueError(
                    f"tarefa {payload.get('task_id')} não existe na lista"
                )
            return DiffItem(
                id=uuid4(),
                target=Target.TASK,
                op=Op.DELETE,
                reason=reason,
                sequence=sequence,
                target_ref=before.id,
                before=before,
            )

        case "REORDER":
            ordered = [str(x) for x in (payload.get("ordered_ids") or [])]
            known = [tid for tid in ordered if tid in existing_tasks]
            if not known:
                raise ValueError("REORDER sem nenhuma tarefa conhecida")
            first = existing_tasks[known[0]]
            return DiffItem(
                id=uuid4(),
                target=Target.TASK,
                op=Op.REORDER,
                reason=reason,
                sequence=sequence,
                target_ref=payload.get("list_id") or first.list_id,
                before=first,
                after=first,
                reorder_ids=tuple(known),
            )

        case "LINK":
            before = existing_tasks.get(str(payload.get("task_id")))
            if before is None:
                raise ValueError(
                    f"tarefa {payload.get('task_id')} não existe na lista"
                )
            refs = refs_de_blocos()
            if not refs:
                # Todos os blocos sumiram entre propor e aprovar. Sem
                # nenhum, o item não tem o que fazer: `LINK` sem destino
                # seria um cartão que aplica e não muda nada.
                raise ValueError(
                    f"'{before.title}': nenhum dos blocos existe mais"
                )
            return DiffItem(
                id=uuid4(),
                target=Target.TASK,
                op=Op.LINK,
                reason=reason,
                sequence=sequence,
                target_ref=before.id,
                event_refs=refs,
            )

        case _:
            raise ValueError(f"operação de tarefa não suportada: {op}")


# ---------------------------------------------------------------
# Persistência
# ---------------------------------------------------------------
def _payload_json(value: Event | Task | None) -> dict | None:
    """Serializa o lado `before`/`after`, que pode ser evento OU tarefa.

    A chave `_kind` é o discriminador na volta. Sem ela, um `Task` sem
    prazo e um `Event` malformado seriam indistinguíveis na leitura, e
    o item voltaria do banco com o tipo errado — exatamente o que a
    validação de `DiffItem.__post_init__` passou a recusar.
    """
    if value is None:
        return None
    if isinstance(value, Task):
        return _task_json(value)
    return _event_json(value)


def _task_json(task: Task) -> dict:
    return {
        "_kind": "task",
        "id": task.id,
        "list_id": task.list_id,
        "title": task.title,
        "notes": task.notes,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "due_at": task.due_at.isoformat() if task.due_at else None,
        "status": task.status.value,
        "completed_at": (
            task.completed_at.isoformat() if task.completed_at else None
        ),
        "priority": task.priority.value if task.priority else None,
        "labels": list(task.labels),
        "parent_id": task.parent_id,
        "recurrence": task.recurrence,
        "estimated_min": task.estimated_min,
        "position": task.position,
        "source": task.source,
        "etag": task.etag,
        "dropped_fields": list(task.dropped_fields),
    }


def _task_from_json(raw: dict) -> Task:
    estimated = raw.get("estimated_min")
    return Task(
        id=raw.get("id"),
        list_id=raw.get("list_id") or "",
        title=raw.get("title") or "(sem título)",
        notes=raw.get("notes"),
        due_date=(
            date.fromisoformat(raw["due_date"]) if raw.get("due_date") else None
        ),
        due_at=(
            datetime.fromisoformat(raw["due_at"]) if raw.get("due_at") else None
        ),
        status=TaskStatus(raw.get("status", "open")),
        completed_at=(
            datetime.fromisoformat(raw["completed_at"])
            if raw.get("completed_at")
            else None
        ),
        priority=Priority(raw["priority"]) if raw.get("priority") else None,
        labels=tuple(raw.get("labels") or ()),
        parent_id=raw.get("parent_id"),
        recurrence=raw.get("recurrence"),
        estimated=timedelta(minutes=estimated) if estimated else None,
        position=raw.get("position"),
        source=raw.get("source"),
        etag=raw.get("etag"),
        dropped_fields=tuple(raw.get("dropped_fields") or ()),
    )


def _payload_from_json(raw: dict | None) -> Event | Task | None:
    if not raw:
        return None
    if raw.get("_kind") == "task":
        return _task_from_json(raw)
    return _event_from_json(raw)


def _event_json(event: Event | None) -> dict | None:
    if event is None:
        return None
    return {
        "id": event.id,
        "calendar_id": event.calendar_id,
        "title": event.title,
        "start": event.start.isoformat(),
        "end": event.end.isoformat(),
        "all_day": event.all_day,
        "description": event.description,
        "location": event.location,
        "recurrence": event.recurrence,
        "flexibility": event.flexibility.value,
        "etag": event.etag,
        "source": event.source,
        "routine_id": event.routine_id,
    }


def _event_from_json(raw: dict | None) -> Event | None:
    if not raw:
        return None
    return Event(
        id=raw.get("id"),
        calendar_id=raw.get("calendar_id") or "",
        title=raw.get("title") or "(sem título)",
        start=datetime.fromisoformat(raw["start"]),
        end=datetime.fromisoformat(raw["end"]),
        all_day=raw.get("all_day", False),
        description=raw.get("description"),
        location=raw.get("location"),
        recurrence=raw.get("recurrence"),
        flexibility=Flexibility(raw.get("flexibility", "fixed")),
        etag=raw.get("etag"),
        source=raw.get("source"),
        routine_id=raw.get("routine_id"),
    )


async def _persist(db: AsyncSession, plan: Plan) -> None:
    await db.execute(
        text(
            """
            INSERT INTO core.plans
              (id, user_id, conversation_id, status, summary,
               created_at, expires_at)
            VALUES (:id, :uid, :cid, :status, :summary, :created, :expires)
            """
        ),
        {
            "id": str(plan.id),
            "uid": str(plan.user_id),
            "cid": str(plan.conversation_id) if plan.conversation_id else None,
            "status": plan.status.value,
            "summary": plan.summary,
            "created": plan.created_at,
            "expires": plan.expires_at,
        },
    )

    for item in plan.items:
        await db.execute(
            text(
                """
                INSERT INTO core.plan_items
                  (id, user_id, plan_id, target, op, target_ref,
                   before, after, reason, confidence, conflicts, links,
                   sequence, status, reorder_ids, event_refs)
                VALUES (:id, :uid, :pid, :target, :op, :ref,
                        CAST(:before AS json), CAST(:after AS json),
                        :reason, :conf, CAST(:conflicts AS json),
                        CAST(:links AS json), :seq, :status,
                        CAST(:reorder AS json), CAST(:event_refs AS json))
                """
            ),
            {
                "id": str(item.id),
                "uid": str(plan.user_id),
                "pid": str(plan.id),
                "target": item.target.value,
                "op": item.op.value,
                "ref": item.target_ref,
                "before": _json(_payload_json(item.before)),
                "after": _json(_payload_json(item.after)),
                "reason": item.reason,
                "conf": item.confidence,
                "conflicts": _json(
                    [
                        {
                            "kind": c.kind.value,
                            "message": c.message,
                            "severity": c.severity,
                        }
                        for c in item.conflicts
                    ]
                ),
                "links": _json([str(x) for x in item.links]),
                "seq": item.sequence,
                "status": item.status.value,
                "reorder": _json(list(item.reorder_ids)),
                # Lista de pares, não de ids: `(kind, id)`, porque o id
                # sozinho não diz em qual das duas tabelas de evento
                # procurar (ver a migração 0011).
                "event_refs": _json(
                    [[kind, ref] for kind, ref in item.event_refs]
                ),
            },
        )


def _json(value) -> str | None:
    import json

    return json.dumps(value, ensure_ascii=False) if value is not None else None


async def load_plan(
    db: AsyncSession, user_id: UUID, plan_id: UUID
) -> Plan | None:
    row = (
        await db.execute(
            text(
                """
                SELECT conversation_id, origin_note, status, summary,
                       created_at, expires_at
                FROM core.plans WHERE id = :pid AND user_id = :uid
                """
            ),
            {"pid": str(plan_id), "uid": str(user_id)},
        )
    ).first()

    if row is None:
        return None

    plan = Plan(
        id=plan_id,
        user_id=user_id,
        conversation_id=row[0],
        origin_note=row[1],
        status=PlanStatus(row[2]),
        summary=row[3] or "",
        created_at=row[4],
        expires_at=row[5],
    )

    items = await db.execute(
        text(
            """
            SELECT id, target, op, target_ref, before, after, reason,
                   confidence, conflicts, links, sequence, status,
                   reorder_ids, event_refs
            FROM core.plan_items
            WHERE plan_id = :pid AND user_id = :uid
            ORDER BY sequence
            """
        ),
        {"pid": str(plan_id), "uid": str(user_id)},
    )

    for r in items.all():
        plan.items.append(
            DiffItem(
                id=r[0],
                target=Target(r[1]),
                op=Op(r[2]),
                target_ref=r[3],
                before=_payload_from_json(r[4]),
                after=_payload_from_json(r[5]),
                reason=r[6],
                confidence=r[7] or 1.0,
                conflicts=tuple(
                    Conflict(
                        kind=ConflictKind(c["kind"]),
                        message=c["message"],
                        severity=c.get("severity", "warning"),
                    )
                    for c in (r[8] or [])
                ),
                links=tuple(UUID(x) for x in (r[9] or [])),
                sequence=r[10],
                status=ItemStatus(r[11]),
                reorder_ids=tuple(r[12] or ()),
                event_refs=tuple(
                    (par[0], par[1]) for par in (r[13] or ())
                ),
            )
        )

    return plan


async def mark_applied(
    db: AsyncSession, user_id: UUID, plan_id: UUID, result
) -> None:
    await db.execute(
        text(
            """
            UPDATE core.plans
            SET status = :status, applied_at = now()
            WHERE id = :pid AND user_id = :uid
            """
        ),
        {
            "pid": str(plan_id),
            "uid": str(user_id),
            "status": result.status.value,
        },
    )

    for record in result.applied:
        await db.execute(
            text(
                """
                UPDATE core.plan_items
                SET status = 'applied', applied_ref = :ref, applied_at = now()
                WHERE id = :iid AND user_id = :uid
                """
            ),
            {
                "iid": str(record.item_id),
                "uid": str(user_id),
                "ref": record.applied_ref,
            },
        )

    for item_id, error in result.failed:
        await db.execute(
            text(
                """
                UPDATE core.plan_items
                SET status = 'failed', error = :err
                WHERE id = :iid AND user_id = :uid
                """
            ),
            {"iid": str(item_id), "uid": str(user_id), "err": error[:500]},
        )


async def mark_rejected(
    db: AsyncSession, user_id: UUID, plan_id: UUID
) -> None:
    await db.execute(
        text(
            "UPDATE core.plans SET status = 'rejected' "
            "WHERE id = :pid AND user_id = :uid"
        ),
        {"pid": str(plan_id), "uid": str(user_id)},
    )
    await db.execute(
        text(
            "UPDATE core.plan_items SET status = 'rejected' "
            "WHERE plan_id = :pid AND user_id = :uid AND status = 'pending'"
        ),
        {"pid": str(plan_id), "uid": str(user_id)},
    )


async def record_undo_log(
    db: AsyncSession, user_id: UUID, plan_id: UUID, result
) -> None:
    """Snapshot do estado anterior, para o undo de 7 dias (§5.8)."""
    for record in result.applied:
        await db.execute(
            text(
                """
                INSERT INTO core.plan_undo_log
                  (user_id, plan_id, item_id, op, snapshot, applied_ref,
                   target)
                VALUES (:uid, :pid, :iid, :op, CAST(:snap AS json), :ref,
                        :target)
                """
            ),
            {
                "uid": str(user_id),
                "pid": str(plan_id),
                "iid": str(record.item_id),
                "op": record.op.value,
                "snap": _json(_payload_json(record.snapshot_before)),
                "ref": record.applied_ref,
                "target": record.target.value,
            },
        )
