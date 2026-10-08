"""Ferramentas de rotina, relatório e mudança externa.

ARQUITETURA.md §5.9.4, §5.9.6, §5.9.9 e §5.10.

Três categorias aparecem aqui, e a distinção importa:

- **Leitura** — aderência, relatório, mudanças externas. Executam.
- **Escrita** — `propose_routine` só acumula no diff. Criar uma série
  escreve N eventos na agenda; aprovar isso é do usuário.
- **Registro de fato** — `mark_routine_occurrence`. "Pulei a academia
  ontem" é fato sobre o passado, não proposta de mudança: tratar como
  plano forçaria o usuário a "aprovar" que faltou (§5.2).

A ferramenta de relatório merece destaque: ela devolve os números E os
padrões já calculados ("3 de 3 no horário 06h–09h foram puladas"). O
modelo cita e interpreta; não soma. É a mesma razão de `find_free_slots`
existir — o que o Age afirma sobre os dados da pessoa precisa ser
verdade.
"""

from __future__ import annotations

from app.domain.tools.registry import ToolCategory, ToolContext, register_tool

_ISO = "data-hora ISO 8601 com fuso, ex. 2026-10-15T19:00:00-03:00"


# ---------------------------------------------------------------
# LEITURA
# ---------------------------------------------------------------
@register_tool(
    name="get_routines",
    description=(
        "Lista as rotinas recorrentes do usuário (academia, leitura, "
        "etc.), com a regra de recorrência, a meta semanal e a próxima "
        "ocorrência. Use quando ele perguntar sobre hábitos ou séries."
    ),
    parameters={"type": "object", "properties": {}},
    category=ToolCategory.READ,
)
async def get_routines(ctx: ToolContext, args: dict) -> dict:
    if ctx.read_calendar is None:
        return {"error": "rotinas não disponíveis neste turno"}
    routines = await ctx.read_calendar("list_routines")
    return {"count": len(routines), "routines": routines}


@register_tool(
    name="get_routine_adherence",
    description=(
        "Aderência das rotinas num período: quantas foram planejadas, "
        "feitas, puladas e movidas. Os números vêm calculados — cite, "
        "não recalcule. "
        "Atenção: MOVIDA não é falta. Quem mudou a academia de terça "
        "para quinta e foi, cumpriu."
    ),
    parameters={
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "janela em dias (padrão 28)",
            }
        },
    },
    category=ToolCategory.READ,
)
async def get_routine_adherence(ctx: ToolContext, args: dict) -> dict:
    if ctx.read_calendar is None:
        return {"error": "rotinas não disponíveis neste turno"}
    data = await ctx.read_calendar(
        "routine_adherence", days=args.get("days", 28)
    )
    return {"adherence": data}


@register_tool(
    name="get_period_report",
    description=(
        "Relatório de um período: planejado vs. realizado em estudo, "
        "rotinas e agenda, mais os PADRÕES encontrados no que foi "
        "pulado. Use para 'como foi minha semana?'. "
        "Regras ao responder: dado antes de interpretação; interprete "
        "só o que o dado sustenta; e NUNCA elogie quando os números não "
        "justificam — 'parabéns' com 2 de 6 sessões feitas destrói a "
        "confiança no resto do que você diz."
    ),
    parameters={
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "quantos dias olhar para trás (padrão 7)",
            }
        },
    },
    category=ToolCategory.READ,
)
async def get_period_report(ctx: ToolContext, args: dict) -> dict:
    if ctx.read_calendar is None:
        return {"error": "relatório não disponível neste turno"}
    data = await ctx.read_calendar(
        "period_report", days=args.get("days", 7)
    )
    return data


@register_tool(
    name="get_external_changes",
    description=(
        "Mudanças que o usuário fez na agenda FORA do Age e que ainda "
        "não foram reconhecidas, com o que elas conflitam. "
        "Chame no INÍCIO da conversa quando houver: se algo que a gente "
        "marcou foi atropelado, reconheça isso antes de responder "
        "qualquer outra coisa, e ofereça remanejar."
    ),
    parameters={"type": "object", "properties": {}},
    category=ToolCategory.READ,
)
async def get_external_changes(ctx: ToolContext, args: dict) -> dict:
    if ctx.read_calendar is None:
        return {"error": "sincronização não disponível neste turno"}
    return await ctx.read_calendar("external_changes")


# ---------------------------------------------------------------
# ESCRITA — só acumula no diff
# ---------------------------------------------------------------
@register_tool(
    name="propose_routine",
    description=(
        "PROPÕE uma rotina recorrente com hora marcada. Isto NÃO cria "
        "nada: entra numa proposta que o usuário aprova.\n\n"
        "Antes de chamar: use find_free_slots ou get_events para saber "
        "qual horário colide menos, e PERGUNTE ao usuário se o horário "
        "e a duração servem. Não escolha por ele.\n\n"
        "A rotina nasce FLEXÍVEL, e diga isso: significa que você pode "
        "propor movê-la para abrir espaço para estudo — nunca sem "
        "mostrar antes. Se ele preferir intocável, use "
        "flexibility='fixed'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "ex. 'Academia'"},
            "rrule": {
                "type": "string",
                "description": (
                    "RRULE do RFC 5545, sem DTSTART. Ex. "
                    "'FREQ=WEEKLY;BYDAY=MO,WE,FR' para 3x por semana, "
                    "'FREQ=DAILY' para todo dia"
                ),
            },
            "first_start": {
                "type": "string",
                "description": f"primeira ocorrência, {_ISO}",
            },
            "duration_min": {
                "type": "integer",
                "description": "duração de cada ocorrência, em minutos",
            },
            "flexibility": {
                "type": "string",
                "enum": ["fixed", "semi_flexible", "flexible"],
                "description": "padrão 'flexible' (§5.9.4)",
            },
            "target_per_week": {
                "type": "integer",
                "description": "meta semanal declarada, se houver",
            },
            "reason": {
                "type": "string",
                "description": (
                    "por que este horário — cite o cálculo, ex. "
                    "'seg/qua/sex 19h é o que colide menos'"
                ),
            },
        },
        "required": [
            "title",
            "rrule",
            "first_start",
            "duration_min",
            "reason",
        ],
    },
    category=ToolCategory.WRITE,
)
async def propose_routine(ctx: ToolContext, args: dict) -> dict:
    if ctx.plan_builder is None:
        return {"error": "sem proposta ativa neste turno"}

    item_id = ctx.plan_builder.stage_routine(
        title=args["title"],
        rrule=args["rrule"],
        first_start=args["first_start"],
        duration_min=int(args["duration_min"]),
        reason=args["reason"],
        flexibility=args.get("flexibility", "flexible"),
        target_per_week=args.get("target_per_week"),
    )
    return {
        "staged": True,
        "diff_item_id": item_id,
        "note": (
            "proposta registrada; a série só existe depois da aprovação"
        ),
    }


# ---------------------------------------------------------------
# REGISTRO DE FATO (§5.2)
# ---------------------------------------------------------------
@register_tool(
    name="mark_routine_occurrence",
    description=(
        "Registra o que aconteceu com uma ocorrência de rotina: feita, "
        "pulada ou movida. Executa na hora — NÃO é proposta, porque "
        "'pulei a academia ontem' é fato sobre o passado, e o usuário "
        "não deveria ter que aprovar que faltou.\n\n"
        "Use 'moved' quando ele fez em outro horário, e informe para "
        "quando: movida não conta como falta, e tratar como falta "
        "puniria quem se adaptou em vez de desistir."
    ),
    parameters={
        "type": "object",
        "properties": {
            "routine_id": {"type": "string"},
            "scheduled_start": {
                "type": "string",
                "description": f"horário da ocorrência original, {_ISO}",
            },
            "status": {
                "type": "string",
                "enum": ["done", "skipped", "moved"],
            },
            "actual_min": {
                "type": "integer",
                "description": "quanto tempo de fato durou, se ele disse",
            },
            "moved_to": {
                "type": "string",
                "description": (
                    f"obrigatório quando status='moved', {_ISO}"
                ),
            },
            "notes": {"type": "string"},
        },
        "required": ["routine_id", "scheduled_start", "status"],
    },
    category=ToolCategory.RECORD,
)
async def mark_routine_occurrence(ctx: ToolContext, args: dict) -> dict:
    if ctx.read_calendar is None:
        return {"error": "rotinas não disponíveis neste turno"}

    if args["status"] == "moved" and not args.get("moved_to"):
        return {
            "error": (
                "para marcar como movida, informe `moved_to` — sem isso "
                "não há como distinguir de pulada"
            )
        }

    return await ctx.read_calendar(
        "mark_routine",
        routine_id=args["routine_id"],
        scheduled_start=args["scheduled_start"],
        status=args["status"],
        actual_min=args.get("actual_min"),
        moved_to=args.get("moved_to"),
        notes=args.get("notes"),
    )
