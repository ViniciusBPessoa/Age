"""Ferramentas de estudo.

ARQUITETURA.md §5.5 e §5.9.8. A divisão de trabalho é o ponto central:

**O modelo decompõe** — ele sabe que Álgebra Linear tem espaços
vetoriais e autovalores, e estima quanto tempo cada um leva. Isso exige
conhecimento da disciplina.

**O código fatia e aloca** — quebrar 2h em duas sessões, respeitar
pré-requisito, achar horário livre, espaçar para retenção. Isso exige
aritmética correta.

Um modelo fraco pode estimar mal a duração de um tópico, mas não
consegue agendar duas sessões no mesmo horário nem ignorar um
pré-requisito. É o mesmo princípio de `find_free_slots` (§5.3),
aplicado a um problema maior.
"""

from __future__ import annotations

from app.domain.tools.registry import ToolCategory, ToolContext, register_tool

_ISO = "data-hora ISO 8601 com fuso, ex. 2026-10-20T14:00:00-03:00"


@register_tool(
    name="propose_study_plan",
    description=(
        "Monta um plano de estudo completo a partir dos tópicos que VOCÊ "
        "decompôs. Você fornece os tópicos com estimativa de duração; o "
        "sistema fatia em sessões, respeita pré-requisitos, encontra os "
        "horários livres e distribui para retenção.\n\n"
        "NÃO agenda nada — registra a proposta para o usuário aprovar.\n\n"
        "Use quando a pessoa pedir para estudar um assunto. Antes de "
        "chamar, decomponha o assunto em 4 a 8 tópicos com duração "
        "estimada, e declare os pré-requisitos entre eles quando houver "
        "(não faz sentido estudar autovalores antes de espaços "
        "vetoriais)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "subject": {
                "type": "string",
                "description": "nome da matéria, ex. 'Álgebra Linear'",
            },
            "deadline": {
                "type": "string",
                "description": (
                    f"prazo (prova, entrega), {_ISO}. Nada é agendado no "
                    "dia do prazo"
                ),
            },
            "topics": {
                "type": "array",
                "description": "4 a 8 tópicos, na ordem de estudo",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "estimated_min": {
                            "type": "integer",
                            "description": (
                                "sua estimativa em minutos. Será "
                                "apresentada ao usuário COMO estimativa"
                            ),
                        },
                        "prerequisites": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "nomes de tópicos desta mesma lista que "
                                "precisam vir antes"
                            ),
                        },
                    },
                    "required": ["name", "estimated_min"],
                },
            },
            "include_reviews": {
                "type": "boolean",
                "description": (
                    "acrescentar revisões curtas em D+1/D+3/D+7 "
                    "(padrão: true)"
                ),
            },
            "start_from": {
                "type": "string",
                "description": f"a partir de quando pode agendar, {_ISO}",
            },
        },
        "required": ["subject", "topics"],
    },
    category=ToolCategory.WRITE,
)
async def propose_study_plan(ctx: ToolContext, args: dict) -> dict:
    if ctx.plan_builder is None:
        return {"error": "não é possível propor mudança neste turno"}
    if ctx.read_calendar is None:
        return {"error": "agenda não disponível neste turno"}

    topics = args.get("topics") or []
    if not topics:
        return {"error": "informe ao menos um tópico"}

    result = await ctx.read_calendar(
        "build_study_plan",
        subject=args["subject"],
        topics=topics,
        deadline=args.get("deadline"),
        start_from=args.get("start_from"),
        include_reviews=args.get("include_reviews", True),
    )

    if result.get("error"):
        return result

    # Cada sessão vira um item de proposta.
    staged = []
    for session in result["sessions"]:
        item_id = ctx.plan_builder.stage_create(
            title=session["title"],
            start=session["start"],
            end=session["end"],
            reason=session["reason"],
            flexibility="flexible",
        )
        staged.append(item_id)

    response: dict = {
        "staged": True,
        "session_count": len(staged),
        "diff_item_ids": staged,
        "total_hours": result["total_hours"],
        "days_used": result["days_used"],
        "note": (
            "Proposta registrada. NADA está na agenda até o usuário aprovar."
        ),
    }

    if result.get("slack_days") is not None:
        response["slack_days"] = result["slack_days"]

    # A parte honesta: o que não couber é reportado ao modelo para ele
    # dizer ao usuário, com as opções reais (§5.5 passo 6).
    if not result["fits"]:
        response["fits"] = False
        response["shortfall"] = result["shortfall"]
        response["instruction"] = (
            "NÃO esconda isso. Diga ao usuário quanto ficou de fora e "
            "ofereça as opções: estender o prazo, reduzir a "
            "profundidade de algum tópico, ou abrir um horário "
            "bloqueado."
        )

    return response


@register_tool(
    name="get_study_progress",
    description=(
        "Progresso dos assuntos de estudo: sessões feitas, puladas, horas "
        "reais vs. estimadas. Use para responder sobre andamento, e antes "
        "de reagendar uma sessão perdida."
    ),
    parameters={
        "type": "object",
        "properties": {
            "subject": {
                "type": "string",
                "description": "nome do assunto; omita para ver todos",
            }
        },
    },
    category=ToolCategory.READ,
)
async def get_study_progress(ctx: ToolContext, args: dict) -> dict:
    if ctx.read_calendar is None:
        return {"error": "dados de estudo não disponíveis neste turno"}

    data = await ctx.read_calendar(
        "study_progress", subject=args.get("subject")
    )

    # Quando há 3 faltas no mesmo assunto, o assistente precisa mudar de
    # tom em vez de reagendar de novo (§5.9.8). A instrução vai junto do
    # dado para ele não ignorar.
    for subject in data.get("subjects", []):
        if subject.get("sessions_skipped", 0) >= 3:
            data["instruction"] = (
                f"'{subject['name']}' tem {subject['sessions_skipped']} "
                "sessões puladas. NÃO proponha simplesmente reagendar: "
                "reconheça que a estimativa foi otimista e ofereça "
                "cortar escopo, aceitar que vai apertar, ou perguntar o "
                "que está travando."
            )
            break

    return data


@register_tool(
    name="mark_study_session",
    description=(
        "Registra que uma sessão de estudo foi feita ou pulada. Isto é "
        "FATO sobre o passado, não proposta — é aplicado na hora, sem "
        "aprovação. Informe `actual_min` quando a pessoa disser quanto "
        "tempo levou: é o que me permite calibrar as estimativas."
    ),
    parameters={
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "status": {"type": "string", "enum": ["done", "skipped"]},
            "actual_min": {
                "type": "integer",
                "description": "minutos reais, se a pessoa informou",
            },
            "difficulty": {
                "type": "integer",
                "description": "1 (fácil) a 5 (muito difícil), se informado",
            },
        },
        "required": ["session_id", "status"],
    },
    category=ToolCategory.RECORD,
)
async def mark_study_session(ctx: ToolContext, args: dict) -> dict:
    if ctx.read_calendar is None:
        return {"error": "dados de estudo não disponíveis neste turno"}

    result = await ctx.read_calendar(
        "mark_session",
        session_id=args["session_id"],
        status=args["status"],
        actual_min=args.get("actual_min"),
        difficulty=args.get("difficulty"),
    )

    # Calibração invisível não serve: o usuário precisa saber que o
    # sistema aprende (§5.9.8).
    calibration = result.get("calibration")
    if calibration and calibration.get("direction") != "on_target":
        direction = (
            "mais rápido" if calibration["direction"] == "faster" else "mais devagar"
        )
        result["instruction"] = (
            f"Diga ao usuário que você ajustou a estimativa: ele está "
            f"indo {direction} que o previsto em '{calibration['topic']}', "
            f"e você recalibrou de {calibration['estimated_min']} para "
            f"{calibration['calibrated_min']} minutos nos tópicos "
            f"parecidos."
        )

    if result.get("should_rethink"):
        result["instruction"] = (
            "Esta é a terceira sessão pulada neste assunto. NÃO reagende "
            "de novo sem comentar: reconheça que reagendar não está "
            "resolvendo e ofereça cortar escopo ou perguntar o que está "
            "travando (§5.9.8)."
        )

    return result


@register_tool(
    name="save_preference",
    description=(
        "Guarda uma preferência que o usuário acabou de expressar, para "
        "valer nas próximas vezes. Use quando ele corrigir algo que vai "
        "se repetir: 'não quero nada antes das 10h', 'nunca no fim de "
        "semana', 'sessões de 1h no máximo'.\n\n"
        "Pergunte antes de salvar — correção pontual não é "
        "necessariamente regra permanente."
    ),
    parameters={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": (
                    "identificador curto, ex. 'no_study_before',"
                    " 'no_weekend_study', 'max_session_min'"
                ),
            },
            "value": {
                "description": "o valor, ex. '10:00', true, 60",
            },
            "confirmed": {
                "type": "boolean",
                "description": (
                    "true só se o usuário confirmou que é permanente"
                ),
            },
        },
        "required": ["key", "value"],
    },
    category=ToolCategory.RECORD,
)
async def save_preference(ctx: ToolContext, args: dict) -> dict:
    if ctx.read_calendar is None:
        return {"error": "não disponível neste turno"}

    await ctx.read_calendar(
        "save_preference",
        key=args["key"],
        value=args["value"],
        confirmed=args.get("confirmed", False),
    )
    return {
        "saved": True,
        "key": args["key"],
        "permanent": args.get("confirmed", False),
    }
