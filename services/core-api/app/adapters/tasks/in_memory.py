"""Listas em memória — para testes.

ARQUITETURA.md §3.3.3. Duas classes, e a segunda é a mais importante:

- `InMemoryTasks` — capacidades plenas, o provedor de referência.
- `PoorInMemoryTasks` — capacidades **mínimas**, deliberadamente.

A segunda existe porque §3.3.1 observa que o `GoogleTasks` tem valor
especial na suíte de contrato justamente por ser o mais pobre: não
guarda hora no prazo, nem prioridade, nem duração, nem faz sync
incremental. Se a abstração aguenta ele sem vazar `if provider ==
"google"` para fora de `adapters/`, aguenta qualquer um.

O problema é que esse adaptador só nasce quando houver OAuth do Google,
e até lá o caminho de degradação (§3.3.2) ficaria **sem nenhum teste** —
exatamente o caminho mais fácil de implementar errado, porque o erro é
silencioso: o campo simplesmente desaparece e ninguém percebe até o
usuário reclamar.

`PoorInMemoryTasks` declara as mesmas limitações do Google Tasks e roda
na suíte hoje. Quando o `GoogleTasks` real chegar, ele entra numa suíte
que já sabe cobrar o comportamento correto.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import uuid4

from app.adapters.tasks.registry import register_tasks
from app.ports.calendar import SyncCapability, SyncCursor
from app.ports.tasks import (
    Task,
    TaskCapabilities,
    TaskConcurrencyConflict,
    TaskListNotFound,
    TaskListRef,
    TaskNotFound,
    TaskNotSupported,
    TaskProvider,
    TaskStatus,
    TaskSyncResult,
    degrade_for,
)

_FULL_CAPS = TaskCapabilities(
    supports_due_time=True,
    supports_estimated_duration=True,
    supports_priority=True,
    supports_labels=True,
    supports_subtasks=True,
    supports_recurrence=True,
    supports_notes=True,
    supports_reorder=True,
    supports_etag_concurrency=True,
    sync=SyncCapability.INCREMENTAL,
    max_title_length=None,
)

# As limitações reais do Google Tasks, de §3.3.1. A documentação é
# explícita sobre a hora: "Only date information is recorded; the time
# portion of the timestamp is discarded when setting this field."
_POOR_CAPS = TaskCapabilities(
    supports_due_time=False,
    supports_estimated_duration=False,
    supports_priority=False,
    supports_labels=False,
    supports_subtasks=True,  # via `parent`
    supports_recurrence=False,
    supports_notes=True,
    supports_reorder=True,  # por posição, não por prioridade
    supports_etag_concurrency=False,
    sync=SyncCapability.POLL_ONLY,
    max_title_length=1024,
)


class _BaseInMemoryTasks(TaskProvider):
    """Implementação comum; as subclasses só trocam as capacidades."""

    def __init__(self) -> None:
        self._lists: dict[str, TaskListRef] = {}
        self._tasks: dict[str, Task] = {}
        self._deleted: list[tuple[str, datetime]] = []
        self._revision = 0
        self._task_revision: dict[str, int] = {}

        default = TaskListRef(id="default", name="Tarefas", is_default=True)
        self._lists[default.id] = default

    @property
    def capabilities(self) -> TaskCapabilities:  # pragma: no cover
        raise NotImplementedError

    # -----------------------------------------------------------
    async def list_task_lists(self) -> list[TaskListRef]:
        return sorted(
            self._lists.values(), key=lambda r: (not r.is_default, r.name)
        )

    async def list_tasks(
        self,
        list_ids: list[str],
        *,
        include_done: bool = False,
        due_before: date | None = None,
    ) -> list[Task]:
        wanted = set(list_ids) if list_ids else set(self._lists)
        out = [
            t
            for t in self._tasks.values()
            if t.list_id in wanted
            and (include_done or t.status == TaskStatus.OPEN)
            and (
                due_before is None
                or (t.due_date is not None and t.due_date <= due_before)
            )
        ]
        return sorted(
            out,
            key=lambda t: (
                t.due_date is None,
                t.due_date or date.max,
                t.position if t.position is not None else 0,
            ),
        )

    async def create_task(self, task: Task) -> Task:
        if task.list_id and task.list_id not in self._lists:
            raise TaskListNotFound(f"lista {task.list_id} não existe")

        caps = self.capabilities
        if caps.max_title_length and len(task.title) > caps.max_title_length:
            raise TaskNotSupported(
                f"{self.key}: título passa de {caps.max_title_length} "
                "caracteres"
            )

        stored = self._degrade(task)
        task_id = str(uuid4())
        self._revision += 1
        stored = replace(
            stored,
            id=task_id,
            list_id=task.list_id or "default",
            position=len(self._tasks),
            etag=str(self._revision) if caps.supports_etag_concurrency else None,
        )
        self._tasks[task_id] = stored
        self._task_revision[task_id] = self._revision
        return stored

    async def update_task(
        self, task: Task, expected_etag: str | None = None
    ) -> Task:
        if task.id is None or task.id not in self._tasks:
            raise TaskNotFound(f"tarefa {task.id} não existe")

        caps = self.capabilities
        if (
            caps.supports_etag_concurrency
            and expected_etag is not None
            and self._tasks[task.id].etag != expected_etag
        ):
            raise TaskConcurrencyConflict(
                f"tarefa {task.id} mudou desde a leitura"
            )

        self._revision += 1
        stored = replace(
            self._degrade(task),
            etag=str(self._revision) if caps.supports_etag_concurrency else None,
        )
        self._tasks[task.id] = stored
        self._task_revision[task.id] = self._revision
        return stored

    async def complete_task(self, list_id: str, task_id: str) -> Task:
        current = self._tasks.get(task_id)
        if current is None:
            raise TaskNotFound(f"tarefa {task_id} não existe")
        self._revision += 1
        done = replace(
            current,
            status=TaskStatus.DONE,
            completed_at=current.completed_at or datetime.now(UTC),
        )
        self._tasks[task_id] = done
        self._task_revision[task_id] = self._revision
        return done

    async def delete_task(self, list_id: str, task_id: str) -> None:
        if task_id in self._tasks:
            del self._tasks[task_id]
            self._revision += 1
            self._deleted.append((task_id, datetime.now(UTC)))
        # Idempotente: apagar o que não existe não é erro.

    async def reorder(self, list_id: str, ordered_ids: list[str]) -> None:
        if not self.capabilities.supports_reorder:
            raise TaskNotSupported(f"{self.key} não suporta reordenação")
        for position, task_id in enumerate(ordered_ids):
            current = self._tasks.get(task_id)
            if current is not None:
                self._tasks[task_id] = replace(current, position=position)

    async def sync(
        self, list_id: str, cursor: SyncCursor | None
    ) -> TaskSyncResult:
        if not self.capabilities.supports_incremental_sync:
            # Declarou que não suporta: tem de levantar, não fingir
            # (§10.1). Devolver lista vazia aqui seria a falha
            # silenciosa que a suíte existe para pegar.
            raise TaskNotSupported(
                f"{self.key} não suporta sync incremental"
            )

        since = 0
        if cursor is not None:
            try:
                since = int(cursor.opaque)
            except ValueError:
                return TaskSyncResult(full_resync_required=True)

        changed = tuple(
            t
            for t in self._tasks.values()
            if t.list_id == list_id
            and self._task_revision.get(t.id or "", 0) > since
        )
        return TaskSyncResult(
            changed=changed,
            deleted_ids=tuple(tid for tid, _ in self._deleted),
            next_cursor=SyncCursor(
                opaque=str(self._revision), issued_at=datetime.now(UTC)
            ),
        )

    # -----------------------------------------------------------
    def _degrade(self, task: Task) -> Task:
        """Delega para a porta (§3.3.2).

        A regra vive em `ports.tasks.degrade_for` e não aqui: deixá-la
        em cada adaptador daria a todos eles a chance de esquecer de
        reportar o que descartaram.
        """
        return degrade_for(task, self.capabilities)


@register_tasks
class InMemoryTasks(_BaseInMemoryTasks):
    key = "in_memory"
    display_name = "Em memória (testes)"

    @property
    def capabilities(self) -> TaskCapabilities:
        return _FULL_CAPS


@register_tasks
class PoorInMemoryTasks(_BaseInMemoryTasks):
    """Provedor com as limitações exatas do Google Tasks (§3.3.1).

    Não é um provedor que alguém conecta: é o adversário da suíte de
    contrato. Ver o docstring do módulo para o motivo de ele existir
    antes do adaptador real.
    """

    key = "in_memory_poor"
    display_name = "Em memória, pobre (testes)"

    @property
    def capabilities(self) -> TaskCapabilities:
        return _POOR_CAPS
