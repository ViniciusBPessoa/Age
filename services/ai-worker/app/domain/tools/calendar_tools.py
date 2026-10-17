"""Ferramentas de agenda.

ARQUITETURA.md §5.2. As de leitura consultam a agenda de verdade; as de
escrita só acumulam no diff.

Nota sobre `find_free_slots`: ela é a razão de o produto ser confiável.
O modelo **não inventa horário livre** — ele chama esta ferramenta, e o
cálculo é determinístico no `Scheduler` do core-api (§5.3). Um modelo
fraco pode escrever uma justificativa ruim, mas não consegue agendar
duas coisas no mesmo horário.
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.domain.tools.registry import ToolCategory, ToolContext, register_tool

log = logging.getLogger("ai-worker.calendar_tools")

_ISO = "data-hora ISO 8601 com fuso, ex. 2026-10-15T14:00:00-03:00"


# ---------------------------------------------------------------
# LEITURA
# ---------------------------------------------------------------
@register_tool(
    name="get_events",
    description=(
        "Lê os compromissos do usuário num período. Use antes de propor "
        "qualquer coisa, para saber o que já existe."
    ),
    parameters={
        "type": "object",
        "properties": {
            "start": {"type": "string", "description": f"início, {_ISO}"},
            "end": {"type": "string", "description": f"fim, {_ISO}"},
        },
        "required": ["start", "end"],
    },
    category=ToolCategory.READ,
)
async def get_events(ctx: ToolContext, args: dict) -> dict:
    if ctx.read_calendar is None:
        return {"error": "agenda não disponível neste turno"}

    events = await ctx.read_calendar(
        "list_events", start=args["start"], end=args["end"]
    )
    return {
        "count": len(events),
        "events": [
            {
                "id": e["id"],
                "title": e["title"],
                "start": e["start"],
                "end": e["end"],
                "flexibility": e["flexibility"],
                # O modelo precisa saber o que NÃO pode mover (§5.6).
                "movable": e["flexibility"] != "fixed",
            }
            for e in events
        ],
    }


@register_tool(
    name="get_pending_proposals",
    description=(
        "Lê o que VOCÊ já propôs e o usuário ainda não aprovou, num "
        "período. `get_events` não mostra isso: ela só devolve o que já "
        "foi aplicado. Use esta ferramenta SEMPRE que o usuário "
        "perguntar o que tem na agenda, o que tem para fazer, ou pedir "
        "para limpar/remarcar o dia — senão você vai dizer que está "
        "vazio enquanto a tela dele mostra blocos esperando aprovação."
    ),
    parameters={
        "type": "object",
        "properties": {
            "start": {"type": "string", "description": f"início, {_ISO}"},
            "end": {"type": "string", "description": f"fim, {_ISO}"},
        },
        "required": ["start", "end"],
    },
    category=ToolCategory.READ,
)
async def get_pending_proposals(ctx: ToolContext, args: dict) -> dict:
    if ctx.read_calendar is None:
        return {"error": "agenda não disponível neste turno"}

    data = await ctx.read_calendar(
        "pending_proposals", start=args["start"], end=args["end"]
    )
    if not data.get("count"):
        return {"count": 0, "items": [], "hint": "Nada esperando aprovação."}
    return data


@register_tool(
    name="discard_proposals",
    description=(
        "Descarta propostas que ainda esperam aprovação, num período. "
        "Use quando a pessoa disser que não quer mais os blocos "
        "propostos, que mudou de ideia, ou pedir para limpar o que você "
        "sugeriu. NÃO mexe no que já foi aprovado: para remover um "
        "compromisso confirmado, use `propose_delete`. "
        "Informe `title_contains` para apagar só parte: sem ele, TODAS "
        "as propostas do período vão embora."
    ),
    parameters={
        "type": "object",
        "properties": {
            "start": {"type": "string", "description": f"início, {_ISO}"},
            "end": {"type": "string", "description": f"fim, {_ISO}"},
            "title_contains": {
                "type": "string",
                "description": (
                    "opcional: só descarta propostas cujo título contenha "
                    "este texto, ex. 'Função de 1º grau'"
                ),
            },
        },
        "required": ["start", "end"],
    },
    category=ToolCategory.READ,
)
async def discard_proposals(ctx: ToolContext, args: dict) -> dict:
    """Categoria READ embora escreva, e a razão importa.

    `ToolCategory.WRITE` significa "acumula no plano para o usuário
    aprovar" (§5.2). Descartar proposta não pode virar proposta: seria
    pedir aprovação para cancelar algo que ainda nem foi aprovado, e o
    usuário ficaria com duas pendências onde queria zero.

    Nada aqui toca a agenda. O pior caso é descartar demais, e a saída
    é pedir ao Age que proponha de novo.
    """
    if ctx.read_calendar is None:
        return {"error": "agenda não disponível neste turno"}

    data = await ctx.read_calendar(
        "discard_proposals",
        start=args["start"],
        end=args["end"],
        title_contains=args.get("title_contains"),
    )

    # Também o que ESTE turno acabou de encenar.
    #
    # A rota do core-api mexe em `core.plan_items`, ou seja, no que já
    # foi promovido. O que o turno encenou agora vive só no
    # `plan_builder`, e era invisível para ela.
    #
    # Sem isto, "propõe, descarta, propõe de novo" dentro de um turno
    # guardava as DUAS versões: foi assim que um plano de funções em
    # Python ficou com 5 sessões, duas no mesmo horário de sábado,
    # enquanto a resposta dizia "refiz como bloco único".
    no_turno = 0
    if ctx.plan_builder is not None:
        try:
            no_turno = ctx.plan_builder.drop_in_window(
                datetime.fromisoformat(args["start"]),
                datetime.fromisoformat(args["end"]),
                args.get("title_contains"),
            )
        except (ValueError, AttributeError) as exc:
            # Data malformada vinda do modelo não deve derrubar o turno:
            # o descarte do banco já aconteceu e vale.
            log.warning("descarte no staging falhou: %s", exc)

    total = int(data.get("discarded") or 0) + no_turno
    if not total:
        return {
            "discarded": 0,
            "hint": "Não havia proposta pendente nesse período.",
        }
    return {**data, "discarded": total, "discarded_this_turn": no_turno}


@register_tool(
    name="find_free_slots",
    description=(
        "Encontra horários livres que caibam uma duração. SEMPRE use esta "
        "ferramenta para escolher horário — nunca invente um. Ela respeita "
        "o horário de trabalho, as janelas bloqueadas e o limite diário do "
        "usuário, e devolve os slots já ordenados do melhor para o pior."
    ),
    parameters={
        "type": "object",
        "properties": {
            "start": {"type": "string", "description": f"início da busca, {_ISO}"},
            "end": {"type": "string", "description": f"fim da busca, {_ISO}"},
            "duration_min": {
                "type": "integer",
                "description": "duração necessária em minutos",
            },
            "deadline": {
                "type": "string",
                "description": (
                    f"prazo, se houver ({_ISO}). "
                    "Slots depois dele são descartados"
                ),
            },
            "count": {
                "type": "integer",
                "description": "quantos slots devolver (padrão 10)",
            },
        },
        "required": ["start", "end", "duration_min"],
    },
    category=ToolCategory.READ,
)
async def find_free_slots(ctx: ToolContext, args: dict) -> dict:
    if ctx.read_calendar is None:
        return {"error": "agenda não disponível neste turno"}

    slots = await ctx.read_calendar(
        "find_free_slots",
        start=args["start"],
        end=args["end"],
        duration_min=args["duration_min"],
        deadline=args.get("deadline"),
        count=args.get("count", 10),
    )
    if not slots:
        # Resposta útil em vez de lista vazia: o modelo precisa saber
        # POR QUE não achou, para explicar ao usuário (§5.5 passo 6).
        return {
            "count": 0,
            "slots": [],
            "hint": (
                "Nenhum horário livre nessa janela com essa duração. "
                "Considere: janela maior, sessões mais curtas, ou propor "
                "mover um evento flexível."
            ),
        }

    return {
        "count": len(slots),
        "slots": [
            {
                "start": s["start"],
                "end": s["end"],
                "score": round(s["score"], 2),
                "why": s["reasons"],
            }
            for s in slots
        ],
    }


@register_tool(
    name="summarize_workload",
    description=(
        "Quantas horas ocupadas por dia num período, e quais dias passam "
        "do limite do usuário. Use para responder 'minha semana está "
        "pesada?' sem propor mudança."
    ),
    parameters={
        "type": "object",
        "properties": {
            "start": {"type": "string", "description": f"início, {_ISO}"},
            "end": {"type": "string", "description": f"fim, {_ISO}"},
        },
        "required": ["start", "end"],
    },
    category=ToolCategory.READ,
)
async def summarize_workload(ctx: ToolContext, args: dict) -> dict:
    if ctx.read_calendar is None:
        return {"error": "agenda não disponível neste turno"}

    days = await ctx.read_calendar(
        "summarize_workload", start=args["start"], end=args["end"]
    )
    return {
        "days": days,
        "total_hours": round(sum(d["busy_hours"] for d in days), 1),
        "over_limit_days": [d["day"] for d in days if d["over_limit"]],
    }


@register_tool(
    name="get_user_preferences",
    description=(
        "As preferências de agendamento do usuário: horário de trabalho, "
        "janelas bloqueadas, duração de sessão, limite diário."
    ),
    parameters={"type": "object", "properties": {}},
    category=ToolCategory.READ,
)
async def get_user_preferences(ctx: ToolContext, args: dict) -> dict:
    return {"timezone": ctx.timezone, **ctx.preferences}


# ---------------------------------------------------------------
# ESCRITA — só acumulam no diff (§5.2)
# ---------------------------------------------------------------
@register_tool(
    name="propose_event",
    description=(
        "Propõe criar um compromisso. ATENÇÃO: isto NÃO agenda nada — só "
        "registra a proposta para o usuário aprovar. Nunca diga que já "
        "agendou. O horário deve vir de find_free_slots."
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "start": {"type": "string", "description": f"{_ISO}"},
            "end": {"type": "string", "description": f"{_ISO}"},
            "description": {"type": "string"},
            "reason": {
                "type": "string",
                "description": (
                    "por que ESTE horário. Obrigatório — o usuário vê isto "
                    "no cartão de aprovação"
                ),
            },
            "flexibility": {
                "type": "string",
                "enum": ["fixed", "semi_flexible", "flexible"],
                "description": (
                    "quanto você pode mover isto depois. Use 'flexible' "
                    "para blocos de estudo e rotinas; 'fixed' para "
                    "compromissos com outras pessoas"
                ),
            },
        },
        "required": ["title", "start", "end", "reason"],
    },
    category=ToolCategory.WRITE,
)
async def propose_event(ctx: ToolContext, args: dict) -> dict:
    if ctx.plan_builder is None:
        return {"error": "não é possível propor mudança neste turno"}

    item_id = ctx.plan_builder.stage_create(
        title=args["title"],
        start=args["start"],
        end=args["end"],
        reason=args["reason"],
        description=args.get("description"),
        flexibility=args.get("flexibility", "flexible"),
    )
    # `staged`, não `created`: o modelo precisa saber a diferença.
    return {
        "staged": True,
        "diff_item_id": item_id,
        "note": "Proposta registrada. NÃO está na agenda até o usuário aprovar.",
    }


@register_tool(
    name="propose_move",
    description=(
        "Propõe mover um compromisso existente. Só funciona em eventos "
        "com movable=true. NÃO move nada — registra a proposta."
    ),
    parameters={
        "type": "object",
        "properties": {
            "event_id": {"type": "string"},
            "new_start": {"type": "string", "description": f"{_ISO}"},
            "new_end": {"type": "string", "description": f"{_ISO}"},
            "reason": {
                "type": "string",
                "description": (
                    "o que isto abre, e o que custa. Obrigatório — mover "
                    "sem explicar não é permitido"
                ),
            },
        },
        "required": ["event_id", "new_start", "new_end", "reason"],
    },
    category=ToolCategory.WRITE,
)
async def propose_move(ctx: ToolContext, args: dict) -> dict:
    if ctx.plan_builder is None:
        return {"error": "não é possível propor mudança neste turno"}

    item_id = ctx.plan_builder.stage_move(
        event_id=args["event_id"],
        new_start=args["new_start"],
        new_end=args["new_end"],
        reason=args["reason"],
    )
    return {"staged": True, "diff_item_id": item_id}


@register_tool(
    name="propose_delete",
    description=(
        "Propõe remover um compromisso. NÃO remove — registra a proposta."
    ),
    parameters={
        "type": "object",
        "properties": {
            "event_id": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["event_id", "reason"],
    },
    category=ToolCategory.WRITE,
)
async def propose_delete(ctx: ToolContext, args: dict) -> dict:
    if ctx.plan_builder is None:
        return {"error": "não é possível propor mudança neste turno"}

    item_id = ctx.plan_builder.stage_delete(
        event_id=args["event_id"], reason=args["reason"]
    )
    return {"staged": True, "diff_item_id": item_id}


@register_tool(
    name="propose_recurring_event",
    description=(
        "Propõe uma série recorrente (academia 3x por semana, por "
        "exemplo). Use RRULE do padrão iCalendar. NÃO cria nada."
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "first_start": {"type": "string", "description": f"{_ISO}"},
            "duration_min": {"type": "integer"},
            "rrule": {
                "type": "string",
                "description": "ex. RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR",
            },
            "reason": {"type": "string"},
        },
        "required": ["title", "first_start", "duration_min", "rrule", "reason"],
    },
    category=ToolCategory.WRITE,
)
async def propose_recurring_event(ctx: ToolContext, args: dict) -> dict:
    if ctx.plan_builder is None:
        return {"error": "não é possível propor mudança neste turno"}

    item_id = ctx.plan_builder.stage_recurring(
        title=args["title"],
        first_start=args["first_start"],
        duration_min=args["duration_min"],
        rrule=args["rrule"],
        reason=args["reason"],
    )
    return {"staged": True, "diff_item_id": item_id}
