"""Leitura cruzada de tarefas e o vínculo com a agenda.

ARQUITETURA.md §5.9.5 e §5.7.1. Este módulo existe para uma resposta
específica, que é o produto:

```
Das 4 que vencem, só 1 tem tempo reservado:
  ✓ Relatório do TCC     vence 20/10 · 3h reservadas qui
  ⚠ Revisar capítulo 2   vence 10/10 · sem tempo
```

Ela só é possível porque o Age lê **as duas** fontes e sabe qual tarefa
tem bloco. Um app de agenda não sabe das tarefas; um to-do não sabe das
horas livres. É a junção que nenhum dos dois faz sozinho.

Duas responsabilidades:

1. **Agregar** tarefas da lista nativa e das externas (`tasks_shadow`),
   com o vínculo resolvido, para o `Scheduler` e para o prompt.
2. **Manter `task_event_links`** — criar na aplicação do par, apagar no
   undo, e propagar a conclusão de um lado para o outro.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ports.tasks import Priority, Task, TaskStatus

log = logging.getLogger("core-api.task_store")


# Fontes possíveis de uma referência. O par discriminado existe porque
# tarefa pode estar em duas tabelas e evento em outras duas — FK para
# tabelas alternativas não existe em SQL (ver a migração 0008).
LOCAL_TASKS = "local_tasks"
SHADOW_TASKS = "tasks_shadow"
LOCAL_EVENTS = "local_events"
SHADOW_EVENTS = "events_shadow"


@dataclass(frozen=True)
class ReservedBlock:
    """Um bloco de agenda reservado para uma tarefa (§5.7.1)."""

    event_ref_kind: str
    event_ref_id: str
    title: str
    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


@dataclass(frozen=True)
class TaskWithContext:
    """Tarefa + o que o Age sabe dela que o provedor não sabe.

    `reserved` é a razão deste tipo existir: é o que permite dizer
    "3h reservadas quinta" em vez de tratar agenda e lista como coisas
    estranhas uma à outra.
    """

    task: Task
    ref_kind: str
    reserved: tuple[ReservedBlock, ...] = ()
    provider_name: str | None = None

    @property
    def has_reserved_time(self) -> bool:
        return bool(self.reserved)

    @property
    def reserved_minutes(self) -> int:
        return sum(block.minutes for block in self.reserved)


# ---------------------------------------------------------------
# Leitura agregada
# ---------------------------------------------------------------
async def read_tasks(
    db: AsyncSession,
    user_id: UUID,
    *,
    include_done: bool = False,
    due_before: date | None = None,
    unscheduled_only: bool = False,
) -> list[TaskWithContext]:
    """Tarefas das duas fontes, com os blocos reservados resolvidos.

    `unscheduled_only` atende diretamente "mostra o que não tem tempo
    reservado" (§5.9.5) — filtrar no banco, e não em Python, importa
    porque a lista pode ter centenas de itens e a pergunta é sobre
    poucos.
    """
    local = await _read_local(
        db, user_id, include_done=include_done, due_before=due_before
    )
    shadow = await _read_shadow(
        db, user_id, include_done=include_done, due_before=due_before
    )
    everything = local + shadow

    links = await _read_links(db, user_id)
    out: list[TaskWithContext] = []
    for entry in everything:
        reserved = links.get((entry.ref_kind, entry.task.id or ""), ())
        if unscheduled_only and reserved:
            continue
        out.append(
            TaskWithContext(
                task=entry.task,
                ref_kind=entry.ref_kind,
                reserved=reserved,
                provider_name=entry.provider_name,
            )
        )

    # Ordem útil ao usuário: prazo primeiro, sem prazo no fim. Quem não
    # tem prazo não é urgente, mas precisa aparecer — são as 7 sem prazo
    # nenhum de §5.7.3.
    return sorted(
        out,
        key=lambda e: (
            e.task.due_date is None,
            e.task.due_date or date.max,
            -(e.task.priority.rank if e.task.priority else 0),
            e.task.title,
        ),
    )


async def _read_local(
    db: AsyncSession,
    user_id: UUID,
    *,
    include_done: bool,
    due_before: date | None,
) -> list[TaskWithContext]:
    rows = await db.execute(
        text(
            """
            SELECT t.id, t.local_task_list_id, t.title, t.notes,
                   t.due_date, t.due_at, t.status, t.completed_at,
                   t.priority, t.labels, t.parent_id, t.recurrence,
                   t.estimated_min, t.position, t.created_by_age, t.version
            FROM core.local_tasks t
            WHERE t.user_id = :uid
              AND t.deleted_at IS NULL
              AND (:include_done OR t.status = 'open')
              AND (CAST(:due_before AS date) IS NULL
                   OR (t.due_date IS NOT NULL
                       AND t.due_date <= CAST(:due_before AS date)))
            ORDER BY t.due_date NULLS LAST, t.position
            """
        ),
        {
            "uid": str(user_id),
            "include_done": include_done,
            "due_before": due_before,
        },
    )

    out = []
    for r in rows.all():
        out.append(
            TaskWithContext(
                task=Task(
                    id=str(r[0]),
                    list_id=str(r[1]),
                    title=r[2],
                    notes=r[3],
                    due_date=r[4],
                    due_at=r[5],
                    status=TaskStatus(r[6]),
                    completed_at=r[7],
                    priority=Priority(r[8]) if r[8] else None,
                    labels=tuple(r[9] or ()),
                    parent_id=str(r[10]) if r[10] else None,
                    recurrence=r[11],
                    estimated=(
                        timedelta(minutes=r[12]) if r[12] is not None else None
                    ),
                    position=r[13],
                    source="age" if r[14] else None,
                    etag=str(r[15]),
                ),
                ref_kind=LOCAL_TASKS,
                provider_name="Lista do Age",
            )
        )
    return out


async def _read_shadow(
    db: AsyncSession,
    user_id: UUID,
    *,
    include_done: bool,
    due_before: date | None,
) -> list[TaskWithContext]:
    """Tarefas externas, com os campos preservados localmente (§3.3.2).

    A reconstrução usa `COALESCE(local_*, ...)`: o valor que o Age
    guardou vence o que o provedor tem, porque é mais rico. É isso que
    faz a hora e a duração continuarem existindo mesmo conectado ao
    Google Tasks.
    """
    rows = await db.execute(
        text(
            """
            SELECT s.id, s.external_list_id, s.title, s.notes,
                   s.due_date, s.local_due_at, s.status, s.completed_at,
                   s.local_priority, s.local_labels, s.parent_external_id,
                   s.local_recurrence, s.local_estimated_min, s.position,
                   s.etag, c.provider_key, s.local_fields_reason
            FROM core.tasks_shadow s
            JOIN core.task_connections c ON c.id = s.connection_id
            LEFT JOIN core.task_list_selections sel
                   ON sel.connection_id = s.connection_id
                  AND sel.external_list_id = s.external_list_id
            WHERE s.user_id = :uid
              AND s.deleted_at IS NULL
              AND COALESCE(sel.read_enabled, true)
              AND (:include_done OR s.status = 'open')
              AND (CAST(:due_before AS date) IS NULL
                   OR (s.due_date IS NOT NULL
                       AND s.due_date <= CAST(:due_before AS date)))
            ORDER BY s.due_date NULLS LAST, s.position
            """
        ),
        {
            "uid": str(user_id),
            "include_done": include_done,
            "due_before": due_before,
        },
    )

    out = []
    for r in rows.all():
        dropped = tuple(
            (r[16] or "").split(",") if r[16] else ()
        )
        out.append(
            TaskWithContext(
                task=Task(
                    id=str(r[0]),
                    list_id=r[1],
                    title=r[2],
                    notes=r[3],
                    due_date=r[4],
                    due_at=r[5],
                    status=TaskStatus(r[6]),
                    completed_at=r[7],
                    priority=Priority(r[8]) if r[8] else None,
                    labels=tuple(r[9] or ()),
                    parent_id=r[10],
                    recurrence=r[11],
                    estimated=(
                        timedelta(minutes=r[12]) if r[12] is not None else None
                    ),
                    position=r[13],
                    etag=r[14],
                    dropped_fields=tuple(f for f in dropped if f),
                ),
                ref_kind=SHADOW_TASKS,
                provider_name=r[15],
            )
        )
    return out


async def _read_links(
    db: AsyncSession, user_id: UUID
) -> dict[tuple[str, str], tuple[ReservedBlock, ...]]:
    """Blocos reservados, por tarefa.

    Uma consulta só com os dois lados do `UNION`, em vez de uma por
    tarefa: a pergunta de §5.9.5 é sobre a lista inteira, e N+1 aqui
    seria N+1 no caminho mais usado do produto.
    """
    rows = await db.execute(
        text(
            """
            SELECT l.task_ref_kind, l.task_ref_id,
                   l.event_ref_kind, l.event_ref_id,
                   COALESCE(e.title, s.title) AS title,
                   COALESCE(e.start_at, s.start_at) AS start_at,
                   COALESCE(e.end_at, s.end_at) AS end_at
            FROM core.task_event_links l
            -- `event_ref_id` é text porque pode ser uuid (local) ou id
            -- opaco do provedor (shadow). Comparar pelo lado uuid
            -- convertido para text evita o cast que estouraria num id
            -- do Google, que não tem forma de uuid.
            LEFT JOIN core.local_events e
                   ON l.event_ref_kind = 'local_events'
                  AND CAST(e.id AS text) = l.event_ref_id
                  AND e.user_id = l.user_id
                  AND e.deleted_at IS NULL
            LEFT JOIN core.events_shadow s
                   ON l.event_ref_kind = 'events_shadow'
                  AND s.external_event_id = l.event_ref_id
                  AND s.user_id = l.user_id
                  AND s.deleted_at IS NULL
            WHERE l.user_id = :uid AND l.deleted_at IS NULL
            """
        ),
        {"uid": str(user_id)},
    )

    out: dict[tuple[str, str], list[ReservedBlock]] = {}
    for r in rows.all():
        if r[5] is None or r[6] is None:
            # O evento foi apagado fora do Age e o vínculo ficou órfão.
            # Ignorar na leitura, não apagar aqui: limpar durante um
            # GET esconderia o problema e tornaria a função não-idempotente.
            continue
        out.setdefault((r[0], str(r[1])), []).append(
            ReservedBlock(
                event_ref_kind=r[2],
                event_ref_id=str(r[3]),
                title=r[4] or "(sem título)",
                start=r[5],
                end=r[6],
            )
        )
    return {key: tuple(blocks) for key, blocks in out.items()}


# ---------------------------------------------------------------
# Vínculo (§5.7.1)
# ---------------------------------------------------------------
async def create_link(
    db: AsyncSession,
    user_id: UUID,
    *,
    task_ref_kind: str,
    task_ref_id: str,
    event_ref_kind: str,
    event_ref_id: str,
    plan_id: UUID | None = None,
) -> None:
    """Grava o par. Idempotente pelo índice único parcial."""
    await db.execute(
        text(
            """
            INSERT INTO core.task_event_links
              (user_id, task_ref_kind, task_ref_id,
               event_ref_kind, event_ref_id, created_by_plan_id)
            VALUES (:uid, :tk, :tid, :ek, :eid, CAST(:pid AS uuid))
            ON CONFLICT (task_ref_kind, task_ref_id,
                         event_ref_kind, event_ref_id)
              WHERE deleted_at IS NULL
            DO NOTHING
            """
        ),
        {
            "uid": str(user_id),
            "tk": task_ref_kind,
            "tid": task_ref_id,
            "ek": event_ref_kind,
            "eid": event_ref_id,
            "pid": str(plan_id) if plan_id else None,
        },
    )


async def remove_links_for_plan(
    db: AsyncSession, user_id: UUID, plan_id: UUID
) -> int:
    """Desfaz os vínculos criados por um plano. Usado pelo undo (§5.8)."""
    result = await db.execute(
        text(
            """
            UPDATE core.task_event_links
            SET deleted_at = now()
            WHERE user_id = :uid AND created_by_plan_id = :pid
              AND deleted_at IS NULL
            """
        ),
        {"uid": str(user_id), "pid": str(plan_id)},
    )
    return result.rowcount or 0


async def counterpart_of_event(
    db: AsyncSession, user_id: UUID, event_ref_kind: str, event_ref_id: str
) -> tuple[str, str] | None:
    """A tarefa ligada a um evento, se houver (§5.7.1, razão 2).

    É o que permite "quando você marcar o bloco como feito, eu concluo
    a tarefa junto — não precisa marcar duas vezes". Sem o vínculo, o
    usuário manteria duas listas à mão, que é o problema prático de ter
    duas fontes.
    """
    row = (
        await db.execute(
            text(
                """
                SELECT task_ref_kind, task_ref_id
                FROM core.task_event_links
                WHERE user_id = :uid
                  AND event_ref_kind = :ek AND event_ref_id = :eid
                  AND deleted_at IS NULL
                LIMIT 1
                """
            ),
            {"uid": str(user_id), "ek": event_ref_kind, "eid": event_ref_id},
        )
    ).first()
    return (row[0], str(row[1])) if row else None


async def counterpart_of_task(
    db: AsyncSession, user_id: UUID, task_ref_kind: str, task_ref_id: str
) -> list[tuple[str, str]]:
    """Os eventos ligados a uma tarefa. Plural: pode ter vários blocos."""
    rows = await db.execute(
        text(
            """
            SELECT event_ref_kind, event_ref_id
            FROM core.task_event_links
            WHERE user_id = :uid
              AND task_ref_kind = :tk AND task_ref_id = :tid
              AND deleted_at IS NULL
            """
        ),
        {"uid": str(user_id), "tk": task_ref_kind, "tid": task_ref_id},
    )
    return [(r[0], str(r[1])) for r in rows.all()]


# ---------------------------------------------------------------
# Panorama para o prompt e para a UI (§5.9.5)
# ---------------------------------------------------------------
@dataclass(frozen=True)
class TaskOverview:
    """O resumo que o assistente usa para responder sem alucinar.

    Os números vêm daqui, calculados, e não do modelo. É a mesma
    decisão do `Scheduler`: o LLM não inventa tempo livre, e também não
    inventa quantas tarefas vencem.
    """

    total_open: int
    overdue: tuple[TaskWithContext, ...]
    due_soon: tuple[TaskWithContext, ...]
    without_due: tuple[TaskWithContext, ...]
    unscheduled_due_soon: tuple[TaskWithContext, ...]
    estimated_minutes_unscheduled: int

    @property
    def has_anything(self) -> bool:
        return self.total_open > 0


async def summarize_tasks(
    db: AsyncSession,
    user_id: UUID,
    *,
    horizon_days: int = 7,
    now: datetime | None = None,
) -> TaskOverview:
    reference = now or datetime.now(UTC)
    horizon = (reference + timedelta(days=horizon_days)).date()

    everything = await read_tasks(db, user_id)

    overdue = tuple(e for e in everything if e.task.is_overdue(reference))
    overdue_ids = {(e.ref_kind, e.task.id) for e in overdue}

    due_soon = tuple(
        e
        for e in everything
        if (e.ref_kind, e.task.id) not in overdue_ids
        and e.task.due_date is not None
        and e.task.due_date <= horizon
    )
    without_due = tuple(e for e in everything if e.task.due_date is None)

    # A lista que gera a pergunta "quer que eu encaixe?": vence logo e
    # não tem bloco. Inclui as atrasadas, porque elas são o caso mais
    # urgente — deixá-las fora faria o resumo omitir o pior.
    unscheduled = tuple(
        e for e in overdue + due_soon if not e.has_reserved_time
    )

    # Soma das estimativas, para responder "cabe?" (§5.9.5). Quem não
    # tem estimativa não entra: inventar uma faria o "cabe" ser um
    # palpite apresentado como conta.
    estimated = sum(
        e.task.estimated_min or 0
        for e in unscheduled
        if e.task.estimated_min
    )

    return TaskOverview(
        total_open=len(everything),
        overdue=overdue,
        due_soon=due_soon,
        without_due=without_due,
        unscheduled_due_soon=unscheduled,
        estimated_minutes_unscheduled=estimated,
    )


def describe_overview(overview: TaskOverview) -> str:
    """Texto compacto do panorama, para o prompt (§5.9.5).

    Formato pensado para o modelo citar sem recalcular: cada linha já
    diz se tem tempo reservado, porque é a distinção que ele não tem
    como inferir.
    """
    if not overview.has_anything:
        return "Lista: nenhuma tarefa aberta."

    lines = [f"Lista: {overview.total_open} tarefas abertas."]

    if overview.overdue:
        lines.append(f"ATRASADAS ({len(overview.overdue)}):")
        for entry in overview.overdue[:10]:
            lines.append("  " + _describe_entry(entry))

    if overview.due_soon:
        lines.append(f"Vencendo em breve ({len(overview.due_soon)}):")
        for entry in overview.due_soon[:10]:
            lines.append("  " + _describe_entry(entry))

    if overview.without_due:
        lines.append(f"Sem prazo: {len(overview.without_due)}.")

    if overview.estimated_minutes_unscheduled:
        hours = overview.estimated_minutes_unscheduled / 60
        lines.append(
            f"Sem tempo reservado, estimativa somada: {hours:.1f}h."
        )

    return "\n".join(lines)


def _describe_entry(entry: TaskWithContext) -> str:
    task = entry.task
    parts = [task.title]

    if task.due_at is not None:
        parts.append(f"vence {task.due_at:%d/%m às %H:%M}")
    elif task.due_date is not None:
        parts.append(f"vence {task.due_date:%d/%m}")
    else:
        parts.append("sem prazo")

    if entry.reserved:
        total = entry.reserved_minutes / 60
        first = entry.reserved[0]
        parts.append(f"{total:.1f}h reservadas {first.start:%d/%m}")
    else:
        parts.append("SEM tempo reservado")

    if task.estimated_min:
        parts.append(f"estimativa {task.estimated_min}min")

    if task.dropped_fields:
        parts.append(
            f"(o provedor não guarda: {', '.join(task.dropped_fields)})"
        )

    return " · ".join(parts)
