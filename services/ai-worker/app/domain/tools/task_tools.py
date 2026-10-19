"""Ferramentas de tarefa.

ARQUITETURA.md §5.2, §5.7.2, §5.7.3 e §5.9.5. As de leitura consultam a
lista de verdade; as de escrita só acumulam no diff — nenhuma delas tem
acesso ao provedor de tarefa, pela mesma razão estrutural das de agenda:
é o que torna impossível uma tool escrever sem aprovação.

A ferramenta mais importante aqui é `get_task_overview`. Ela devolve, já
calculado, qual tarefa tem tempo reservado e qual não tem — a junção que
nenhum app de agenda nem to-do faz sozinho, e que produz a resposta de
§5.9.5:

```
Das 4 que vencem, só 1 tem tempo reservado:
  ✓ Relatório do TCC     vence 20/10 · 3h reservadas qui
  ⚠ Revisar capítulo 2   vence 10/10 · sem tempo
```

O modelo não calcula isso. Ele recebe e conta.
"""

from __future__ import annotations

from app.domain.tools.registry import ToolCategory, ToolContext, register_tool

_ISO = "data-hora ISO 8601 com fuso, ex. 2026-10-15T14:00:00-03:00"
_DATE = "data ISO, ex. 2026-10-15"
_PRIORITIES = ["low", "normal", "high", "urgent"]


# ---------------------------------------------------------------
# LEITURA
# ---------------------------------------------------------------
@register_tool(
    name="get_task_overview",
    description=(
        "Panorama da lista de tarefas: quantas estão abertas, quais "
        "estão atrasadas, quais vencem em breve e — o mais importante — "
        "QUAIS JÁ TÊM TEMPO RESERVADO na agenda e quais não têm. "
        "Use sempre que o usuário perguntar o que ele tem pra fazer. "
        "Os números vêm calculados: não recalcule nem estime."
    ),
    parameters={
        "type": "object",
        "properties": {
            "horizon_days": {
                "type": "integer",
                "description": "quantos dias à frente considerar (padrão 7)",
            }
        },
    },
    category=ToolCategory.READ,
)
async def get_task_overview(ctx: ToolContext, args: dict) -> dict:
    if ctx.read_calendar is None:
        return {"error": "lista não disponível neste turno"}

    data = await ctx.read_calendar(
        "task_overview", horizon_days=args.get("horizon_days", 7)
    )
    return {
        "total_open": data["total_open"],
        "overdue_count": data["overdue_count"],
        "due_soon_count": data["due_soon_count"],
        "without_due_count": data["without_due_count"],
        # A lista que motiva "quer que eu encaixe?". Já filtrada por
        # "vence logo E não tem bloco".
        "unscheduled": data["unscheduled"],
        "estimated_minutes_unscheduled": data[
            "estimated_minutes_unscheduled"
        ],
        "summary": data["summary_text"],
    }


@register_tool(
    name="get_tasks",
    description=(
        "Lista as tarefas do usuário, com prazo, estimativa e se já têm "
        "tempo reservado. Use quando precisar dos itens em si, não só do "
        "resumo — por exemplo para organizar a lista."
    ),
    parameters={
        "type": "object",
        "properties": {
            "include_done": {
                "type": "boolean",
                "description": "incluir concluídas (padrão false)",
            },
            "due_before": {
                "type": "string",
                "description": f"só as que vencem até esta data, {_DATE}",
            },
        },
    },
    category=ToolCategory.READ,
)
async def get_tasks(ctx: ToolContext, args: dict) -> dict:
    if ctx.read_calendar is None:
        return {"error": "lista não disponível neste turno"}

    tasks = await ctx.read_calendar(
        "list_tasks",
        include_done=args.get("include_done", False),
        due_before=args.get("due_before"),
    )
    return {"count": len(tasks), "tasks": tasks}


@register_tool(
    name="get_task_lists",
    description=(
        "Listas de tarefa disponíveis e qual é o destino de escrita. "
        "Use se o usuário mencionar uma lista específica."
    ),
    parameters={"type": "object", "properties": {}},
    category=ToolCategory.READ,
)
async def get_task_lists(ctx: ToolContext, args: dict) -> dict:
    if ctx.read_calendar is None:
        return {"error": "lista não disponível neste turno"}
    lists = await ctx.read_calendar("get_task_lists")
    return {"lists": lists}


# ---------------------------------------------------------------
# ESCRITA — só acumula no diff (§5.2)
# ---------------------------------------------------------------
@register_tool(
    name="create_task",
    description=(
        "PROPÕE uma tarefa nova, sem reservar hora na agenda. Use quando "
        "o usuário quer só anotar algo — 'lembrar de', 'não esquecer', "
        "'põe na lista', ou um prazo sem hora ('até sexta', 'vence dia "
        "20'). Isto NÃO cria nada: entra numa proposta que o usuário "
        "aprova. Nunca diga que já anotou."
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "o que fazer"},
            "notes": {"type": "string", "description": "detalhes, opcional"},
            "due_date": {
                "type": "string",
                "description": f"prazo sem hora, {_DATE}",
            },
            "due_at": {
                "type": "string",
                "description": (
                    f"prazo COM hora, {_ISO}. Use só se o usuário deu "
                    "hora para o prazo — não para reservar tempo de "
                    "trabalho (para isso use reserve_time_for_task)"
                ),
            },
            "priority": {
                "type": "string",
                "enum": _PRIORITIES,
                "description": "prioridade, se o usuário indicou",
            },
            "estimated_min": {
                "type": "integer",
                "description": (
                    "quanto tempo você estima que leva, em minutos. "
                    "Importa: é o que permite responder se cabe na semana"
                ),
            },
            "list_id": {
                "type": "string",
                "description": "id da lista, se o usuário escolheu uma",
            },
            "reason": {
                "type": "string",
                "description": "por que esta tarefa, nas palavras do usuário",
            },
            "tema": {
                "type": "string",
                "description": (
                    "assunto que agrupa esta tarefa com as irmãs, como "
                    "o usuário diria: 'Funções em Python', 'TCC', "
                    "'Academia'. NÃO é só para estudo. É o que faz a "
                    "tela agrupar por tema; sem ele a tarefa cai em "
                    "'Sem tema'"
                ),
            },
            "cobre_eventos": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "diff_item_id das sessões JÁ PROPOSTAS NESTE TURNO "
                    "que cobrem esta tarefa — use os ids devolvidos por "
                    "`propose_event` ou os de `diff_item_ids` de "
                    "`propose_study_plan`. É o que liga o to-do ao "
                    "horário. Nunca escreva essa ligação na observação "
                    "em texto: só vale o que entra aqui"
                ),
            },
        },
        "required": ["title", "reason"],
    },
    category=ToolCategory.WRITE,
)
async def create_task(ctx: ToolContext, args: dict) -> dict:
    if ctx.plan_builder is None:
        return {"error": "sem proposta ativa neste turno"}

    item_id = ctx.plan_builder.stage_task_create(
        title=args["title"],
        reason=args["reason"],
        notes=args.get("notes"),
        due_date=args.get("due_date"),
        due_at=args.get("due_at"),
        priority=args.get("priority"),
        estimated_min=args.get("estimated_min"),
        list_id=args.get("list_id"),
        tema=args.get("tema"),
        cobre_eventos=args.get("cobre_eventos"),
    )
    # `staged`, não `created`: o modelo precisa saber a diferença, e o
    # system prompt reforça que ele não deve afirmar "já anotei".
    return {
        "staged": True,
        "diff_item_id": item_id,
        "note": "proposta registrada; o usuário ainda vai aprovar",
    }


@register_tool(
    name="update_task",
    description=(
        "PROPÕE mudar uma tarefa: título, prazo, prioridade, estimativa "
        "ou lista. Use para repactuar prazo de tarefa atrasada. Não "
        "aplica nada sozinho."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "id da tarefa"},
            "title": {"type": "string"},
            "due_date": {"type": "string", "description": _DATE},
            "due_at": {"type": "string", "description": _ISO},
            "priority": {"type": "string", "enum": _PRIORITIES},
            "estimated_min": {"type": "integer"},
            "list_id": {"type": "string"},
            "reason": {
                "type": "string",
                "description": "por que esta mudança — obrigatório",
            },
        },
        "required": ["task_id", "reason"],
    },
    category=ToolCategory.WRITE,
)
async def update_task(ctx: ToolContext, args: dict) -> dict:
    if ctx.plan_builder is None:
        return {"error": "sem proposta ativa neste turno"}

    item_id = ctx.plan_builder.stage_task_update(
        task_id=args["task_id"],
        reason=args["reason"],
        title=args.get("title"),
        due_date=args.get("due_date"),
        due_at=args.get("due_at"),
        priority=args.get("priority"),
        estimated_min=args.get("estimated_min"),
        list_id=args.get("list_id"),
    )
    return {"staged": True, "diff_item_id": item_id}


@register_tool(
    name="complete_task",
    description=(
        "PROPÕE marcar uma tarefa como feita. Use SOMENTE quando o "
        "usuário disser explicitamente que fez ou que quer marcar. "
        "NUNCA conclua por inferência: se você acha que algo pode ter "
        "sido feito, PERGUNTE — 'você comprou o cabo?' — e espere a "
        "resposta. Concluir algo que a pessoa não fez destrói a "
        "confiança na lista dela."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "reason": {
                "type": "string",
                "description": (
                    "cite o que o usuário disse que justifica concluir"
                ),
            },
        },
        "required": ["task_id", "reason"],
    },
    category=ToolCategory.WRITE,
)
async def complete_task(ctx: ToolContext, args: dict) -> dict:
    if ctx.plan_builder is None:
        return {"error": "sem proposta ativa neste turno"}

    item_id = ctx.plan_builder.stage_task_complete(
        task_id=args["task_id"], reason=args["reason"]
    )
    return {"staged": True, "diff_item_id": item_id}


@register_tool(
    name="delete_task",
    description="PROPÕE remover uma tarefa da lista. Não aplica sozinho.",
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["task_id", "reason"],
    },
    category=ToolCategory.WRITE,
)
async def delete_task(ctx: ToolContext, args: dict) -> dict:
    if ctx.plan_builder is None:
        return {"error": "sem proposta ativa neste turno"}
    item_id = ctx.plan_builder.stage_task_delete(
        task_id=args["task_id"], reason=args["reason"]
    )
    return {"staged": True, "diff_item_id": item_id}


@register_tool(
    name="reorder_tasks",
    description=(
        "PROPÕE reordenar a lista. Passe os ids NA ORDEM desejada; as "
        "tarefas que você não citar mantêm a ordem relativa que têm e "
        "vão depois. Use para 'organiza minha lista'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "list_id": {"type": "string"},
            "ordered_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "ids das tarefas, na ordem desejada",
            },
            "reason": {
                "type": "string",
                "description": "o critério que você usou para ordenar",
            },
        },
        "required": ["list_id", "ordered_ids", "reason"],
    },
    category=ToolCategory.WRITE,
)
async def reorder_tasks(ctx: ToolContext, args: dict) -> dict:
    if ctx.plan_builder is None:
        return {"error": "sem proposta ativa neste turno"}
    ordered = args.get("ordered_ids") or []
    if not ordered:
        return {"error": "ordered_ids não pode ser vazio"}
    item_id = ctx.plan_builder.stage_task_reorder(
        list_id=args["list_id"],
        ordered_ids=ordered,
        reason=args["reason"],
    )
    return {"staged": True, "diff_item_id": item_id}


@register_tool(
    name="reserve_time_for_task",
    description=(
        "PROPÕE reservar um bloco na agenda PARA uma tarefa existente, "
        "criando um par ligado: o bloco na agenda e a tarefa apontando "
        "para ele. Use quando o usuário pedir para 'encaixar', "
        "'reservar tempo' ou 'achar um horário pra' algo que já está na "
        "lista. SEMPRE use find_free_slots antes para escolher o "
        "horário — nunca invente um. "
        "Depois de aplicado, concluir a TAREFA faz o Age lembrar do "
        "bloco e oferecer liberar o horário. O contrário não existe: "
        "não há como marcar o bloco como feito, então nunca prometa "
        "isso."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "id da tarefa"},
            "title": {
                "type": "string",
                "description": "título do bloco na agenda",
            },
            "start": {"type": "string", "description": f"início, {_ISO}"},
            "end": {"type": "string", "description": f"fim, {_ISO}"},
            "reason": {
                "type": "string",
                "description": (
                    "por que este horário — cite o cálculo, ex. 'antes "
                    "do prazo, de manhã como você prefere'"
                ),
            },
        },
        "required": ["task_id", "title", "start", "end", "reason"],
    },
    category=ToolCategory.WRITE,
)
async def reserve_time_for_task(ctx: ToolContext, args: dict) -> dict:
    if ctx.plan_builder is None:
        return {"error": "sem proposta ativa neste turno"}

    ids = ctx.plan_builder.stage_time_for_task(
        task_id=args["task_id"],
        title=args["title"],
        start=args["start"],
        end=args["end"],
        reason=args["reason"],
    )
    return {
        "staged": True,
        "linked_pair": ids,
        "note": (
            "dois itens ligados: o bloco e a tarefa. O usuário aprova "
            "os dois juntos"
        ),
    }
