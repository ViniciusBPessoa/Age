"""Lista de tarefas nativa do Age — adaptador de primeira classe.

ARQUITETURA.md §3.3.3. **Não é um stub de teste.** É o provedor default
de uma conta nova, pelas mesmas três razões do calendário nativo
(§3.2.1): onboarding sem OAuth, prova de que a porta é real, e ter sido
escrito ANTES do Google — construir a porta tendo o Google Tasks como
única referência é como ela acabaria sem `due_at`, sem prioridade e sem
duração, que é exatamente o que §3.3.1 documenta.

Capacidades plenas. É o provedor contra o qual os outros são medidos.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.tasks.registry import register_tasks
from app.ports.calendar import SyncCapability, SyncCursor
from app.ports.tasks import (
    Priority,
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

_CAPS = TaskCapabilities(
    supports_due_time=True,
    supports_estimated_duration=True,
    supports_priority=True,
    supports_labels=True,
    supports_subtasks=True,
    supports_recurrence=True,
    supports_notes=True,
    supports_reorder=True,
    supports_etag_concurrency=True,  # via coluna `version`
    sync=SyncCapability.INCREMENTAL,
    max_title_length=None,
)

_COLS = """
    id, local_task_list_id, title, notes, due_date, due_at, status,
    completed_at, priority, labels, parent_id, recurrence,
    estimated_min, position, created_by_age, version
"""


@register_tasks
class LocalTasks(TaskProvider):
    key = "local"
    display_name = "Lista do Age"

    def __init__(self, *, db: AsyncSession, user_id: UUID) -> None:
        self._db = db
        self._user_id = user_id

    @property
    def capabilities(self) -> TaskCapabilities:
        return _CAPS

    # -----------------------------------------------------------
    # Listas
    # -----------------------------------------------------------
    async def ensure_default_list(self) -> TaskListRef:
        """Cria a lista padrão se a conta ainda não tem.

        Chamada no primeiro acesso, não no registro: o cadastro não deve
        falhar por causa de algo secundário (mesma decisão de
        `LocalCalendar.ensure_default_calendar`).
        """
        row = (
            await self._db.execute(
                text(
                    """
                    SELECT id, name, is_default, color
                    FROM core.local_task_lists
                    WHERE user_id = :uid
                    ORDER BY is_default DESC, created_at ASC
                    LIMIT 1
                    """
                ),
                {"uid": str(self._user_id)},
            )
        ).first()

        if row is not None:
            return TaskListRef(
                id=str(row[0]), name=row[1], is_default=row[2], color=row[3]
            )

        created = (
            await self._db.execute(
                text(
                    """
                    INSERT INTO core.local_task_lists
                      (user_id, name, is_default)
                    VALUES (:uid, 'Minhas tarefas', true)
                    RETURNING id, name, is_default, color
                    """
                ),
                {"uid": str(self._user_id)},
            )
        ).first()
        assert created is not None
        return TaskListRef(
            id=str(created[0]),
            name=created[1],
            is_default=created[2],
            color=created[3],
        )

    async def list_task_lists(self) -> list[TaskListRef]:
        rows = await self._db.execute(
            text(
                """
                SELECT id, name, is_default, color
                FROM core.local_task_lists
                WHERE user_id = :uid
                ORDER BY is_default DESC, name
                """
            ),
            {"uid": str(self._user_id)},
        )
        return [
            TaskListRef(
                id=str(r[0]), name=r[1], is_default=r[2], color=r[3]
            )
            for r in rows.all()
        ]

    async def create_task_list(
        self, name: str, *, color: str | None = None
    ) -> TaskListRef:
        """Fora do contrato da porta: nem todo provedor cria lista."""
        row = (
            await self._db.execute(
                text(
                    """
                    INSERT INTO core.local_task_lists (user_id, name, color)
                    VALUES (:uid, :name, :color)
                    RETURNING id, name, is_default, color
                    """
                ),
                {"uid": str(self._user_id), "name": name, "color": color},
            )
        ).first()
        assert row is not None
        return TaskListRef(
            id=str(row[0]), name=row[1], is_default=row[2], color=row[3]
        )

    # -----------------------------------------------------------
    # Tarefas
    # -----------------------------------------------------------
    async def list_tasks(
        self,
        list_ids: list[str],
        *,
        include_done: bool = False,
        due_before: date | None = None,
    ) -> list[Task]:
        rows = await self._db.execute(
            text(
                f"""
                SELECT {_COLS}
                FROM core.local_tasks
                WHERE user_id = :uid
                  AND deleted_at IS NULL
                  AND (CAST(:lists AS json) IS NULL
                       OR CAST(local_task_list_id AS text) IN (
                            SELECT json_array_elements_text(
                                     CAST(:lists AS json))))
                  AND (:include_done OR status = 'open')
                  AND (CAST(:due_before AS date) IS NULL
                       OR (due_date IS NOT NULL
                           AND due_date <= CAST(:due_before AS date)))
                ORDER BY due_date NULLS LAST, position, created_at
                """
            ),
            {
                "uid": str(self._user_id),
                # `None` significa "todas as listas". Passar o array
                # vazio significaria "nenhuma", e confundir os dois é
                # como uma consulta volta vazia sem motivo aparente.
                "lists": _json(list_ids) if list_ids else None,
                "include_done": include_done,
                "due_before": due_before,
            },
        )
        return [self._to_task(r) for r in rows.all()]

    async def get_task(self, task_id: str) -> Task:
        row = (
            await self._db.execute(
                text(
                    f"""
                    SELECT {_COLS}
                    FROM core.local_tasks
                    WHERE id = :tid AND user_id = :uid AND deleted_at IS NULL
                    """
                ),
                {"tid": task_id, "uid": str(self._user_id)},
            )
        ).first()
        if row is None:
            raise TaskNotFound(f"tarefa {task_id} não existe")
        return self._to_task(row)

    async def create_task(self, task: Task) -> Task:
        list_id = task.list_id or (await self.ensure_default_list()).id

        # Capacidades plenas, então nada é descartado — mas o caminho é
        # o mesmo de todo adaptador. Ter um provedor rico que pula a
        # degradação faria o contrato valer menos do que parece.
        task = degrade_for(task, _CAPS)

        caps = self.capabilities
        if caps.max_title_length and len(task.title) > caps.max_title_length:
            raise TaskNotSupported(
                f"{self.key}: título passa de {caps.max_title_length} "
                "caracteres"
            )

        owns = (
            await self._db.execute(
                text(
                    "SELECT 1 FROM core.local_task_lists "
                    "WHERE id = CAST(:lid AS uuid) AND user_id = :uid"
                ),
                {"lid": list_id, "uid": str(self._user_id)},
            )
        ).scalar_one_or_none()
        if owns is None:
            raise TaskListNotFound(f"lista {list_id} não existe")

        # Nova tarefa entra no fim da lista. Entrar no começo
        # reordenaria o que o usuário já organizou, sem ele pedir.
        row = (
            await self._db.execute(
                text(
                    f"""
                    INSERT INTO core.local_tasks
                      (user_id, local_task_list_id, title, notes,
                       due_date, due_at, status, completed_at, priority,
                       labels, parent_id, recurrence, estimated_min,
                       position, created_by_age)
                    VALUES
                      (:uid, CAST(:lid AS uuid), :title, :notes,
                       CAST(:due_date AS date), CAST(:due_at AS timestamptz),
                       :status, CAST(:completed AS timestamptz),
                       :priority, CAST(:labels AS json),
                       CAST(:parent AS uuid), :rrule,
                       CAST(:est AS integer),
                       COALESCE((SELECT MAX(position) + 1
                                 FROM core.local_tasks
                                 WHERE local_task_list_id = CAST(:lid AS uuid)
                                   AND user_id = :uid
                                   AND deleted_at IS NULL), 0),
                       :by_age)
                    RETURNING {_COLS}
                    """
                ),
                {
                    "uid": str(self._user_id),
                    "lid": list_id,
                    "title": task.title,
                    "notes": task.notes,
                    "due_date": task.due_date,
                    "due_at": task.due_at,
                    "status": task.status.value,
                    "completed": task.completed_at,
                    "priority": task.priority.value if task.priority else None,
                    "labels": _json(list(task.labels)),
                    "parent": task.parent_id,
                    "rrule": task.recurrence,
                    "est": task.estimated_min,
                    "by_age": task.source == "age",
                },
            )
        ).first()
        assert row is not None
        return self._to_task(row)

    async def update_task(
        self, task: Task, expected_etag: str | None = None
    ) -> Task:
        if task.id is None:
            raise TaskNotFound("update exige id")

        expected_version = _parse_etag(expected_etag)

        row = (
            await self._db.execute(
                text(
                    f"""
                    UPDATE core.local_tasks
                    SET title = :title,
                        notes = :notes,
                        due_date = CAST(:due_date AS date),
                        due_at = CAST(:due_at AS timestamptz),
                        status = :status,
                        completed_at = CAST(:completed AS timestamptz),
                        priority = :priority,
                        labels = CAST(:labels AS json),
                        recurrence = :rrule,
                        estimated_min = CAST(:est AS integer),
                        local_task_list_id = CAST(:lid AS uuid),
                        version = version + 1,
                        updated_at = now()
                    WHERE id = :tid AND user_id = :uid
                      AND deleted_at IS NULL
                      AND (CAST(:expected AS integer) IS NULL
                           OR version = CAST(:expected AS integer))
                    RETURNING {_COLS}
                    """
                ),
                {
                    "tid": task.id,
                    "uid": str(self._user_id),
                    "title": task.title,
                    "notes": task.notes,
                    "due_date": task.due_date,
                    "due_at": task.due_at,
                    "status": task.status.value,
                    "completed": task.completed_at,
                    "priority": (
                        task.priority.value if task.priority else None
                    ),
                    "labels": _json(list(task.labels)),
                    "rrule": task.recurrence,
                    "est": task.estimated_min,
                    "lid": task.list_id,
                    "expected": expected_version,
                },
            )
        ).first()

        if row is None:
            # Distingue "não existe" de "mudou": o PlanExecutor reage
            # diferente a cada um (§5.8).
            current = (
                await self._db.execute(
                    text(
                        "SELECT version FROM core.local_tasks "
                        "WHERE id = :tid AND user_id = :uid "
                        "AND deleted_at IS NULL"
                    ),
                    {"tid": task.id, "uid": str(self._user_id)},
                )
            ).scalar_one_or_none()

            if current is None:
                raise TaskNotFound(f"tarefa {task.id} não existe")
            raise TaskConcurrencyConflict(
                f"tarefa {task.id} mudou desde a leitura "
                f"(versão {current}, esperada {expected_version})"
            )

        return self._to_task(row)

    async def complete_task(self, list_id: str, task_id: str) -> Task:
        """Marca como feita, com a hora do fato.

        Idempotente de propósito: concluir o que já está concluído
        preserva o `completed_at` original. Sobrescrever a hora faria o
        relatório de §5.9.9 mentir sobre quando a coisa aconteceu — e
        duplo-clique no botão é o caso normal, não o excepcional.
        """
        row = (
            await self._db.execute(
                text(
                    f"""
                    UPDATE core.local_tasks
                    SET status = 'done',
                        completed_at = COALESCE(completed_at, now()),
                        version = version + 1,
                        updated_at = now()
                    WHERE id = :tid AND user_id = :uid AND deleted_at IS NULL
                    RETURNING {_COLS}
                    """
                ),
                {"tid": task_id, "uid": str(self._user_id)},
            )
        ).first()
        if row is None:
            raise TaskNotFound(f"tarefa {task_id} não existe")
        return self._to_task(row)

    async def reopen_task(self, list_id: str, task_id: str) -> Task:
        """Desfaz a conclusão. Fora do contrato, usado pelo undo (§5.8)."""
        row = (
            await self._db.execute(
                text(
                    f"""
                    UPDATE core.local_tasks
                    SET status = 'open', completed_at = NULL,
                        version = version + 1, updated_at = now()
                    WHERE id = :tid AND user_id = :uid AND deleted_at IS NULL
                    RETURNING {_COLS}
                    """
                ),
                {"tid": task_id, "uid": str(self._user_id)},
            )
        ).first()
        if row is None:
            raise TaskNotFound(f"tarefa {task_id} não existe")
        return self._to_task(row)

    async def delete_task(self, list_id: str, task_id: str) -> None:
        """Soft delete: permite o undo de 7 dias (§5.8).

        Idempotente por contrato — apagar o que não existe não é erro.
        """
        await self._db.execute(
            text(
                """
                UPDATE core.local_tasks
                SET deleted_at = now(), version = version + 1
                WHERE id = :tid AND user_id = :uid AND deleted_at IS NULL
                """
            ),
            {"tid": task_id, "uid": str(self._user_id)},
        )

    async def restore_task(self, task_id: str) -> None:
        """Desfaz um delete. Usado pelo undo (§5.8), fora do contrato."""
        await self._db.execute(
            text(
                """
                UPDATE core.local_tasks
                SET deleted_at = NULL, version = version + 1
                WHERE id = :tid AND user_id = :uid
                """
            ),
            {"tid": task_id, "uid": str(self._user_id)},
        )

    async def reorder(self, list_id: str, ordered_ids: list[str]) -> None:
        """Reposiciona na ordem recebida (§5.7, op `REORDER`).

        Só as tarefas citadas mudam de posição; as não citadas vão para
        depois, preservando a ordem relativa que tinham. "Organiza as 5
        urgentes" não deve embaralhar as outras 18.
        """
        if not ordered_ids:
            return

        for position, task_id in enumerate(ordered_ids):
            await self._db.execute(
                text(
                    """
                    UPDATE core.local_tasks
                    SET position = :pos, version = version + 1,
                        updated_at = now()
                    WHERE id = :tid AND user_id = :uid
                      AND local_task_list_id = CAST(:lid AS uuid)
                      AND deleted_at IS NULL
                    """
                ),
                {
                    "pos": position,
                    "tid": task_id,
                    "uid": str(self._user_id),
                    "lid": list_id,
                },
            )

        # As demais vão para o fim, mantendo a ordem anterior entre si.
        #
        # `:known` chega como JSON e é expandido com `json_array_elements_text`
        # em vez de ir como `uuid[]`: o asyncpg recusa a forma literal de
        # array do Postgres (`'{a,b}'`) num parâmetro tipado, exigindo uma
        # lista Python — e passar lista por `text()` com `CAST(... AS uuid[])`
        # falha com "a sized iterable container expected (got type 'str')".
        # Pelo JSON o caminho é um só e não depende de como o driver trata
        # arrays.
        await self._db.execute(
            text(
                """
                UPDATE core.local_tasks AS t
                SET position = :offset + s.rank
                FROM (
                    SELECT id, ROW_NUMBER() OVER (
                             ORDER BY position, created_at) AS rank
                    FROM core.local_tasks
                    WHERE user_id = :uid
                      AND local_task_list_id = CAST(:lid AS uuid)
                      AND deleted_at IS NULL
                      AND CAST(id AS text) NOT IN (
                            SELECT json_array_elements_text(
                                     CAST(:known AS json)))
                ) AS s
                WHERE t.id = s.id AND t.user_id = :uid
                """
            ),
            {
                "offset": len(ordered_ids),
                "uid": str(self._user_id),
                "lid": list_id,
                "known": _json(ordered_ids),
            },
        )

    # -----------------------------------------------------------
    async def sync(
        self, list_id: str, cursor: SyncCursor | None
    ) -> TaskSyncResult:
        """Sync do provedor nativo: delta por `updated_at`.

        Existe para que a suíte de contrato exercite o caminho de sync
        contra um provedor que o suporta — não porque o front precise
        (ele consulta a própria API). O cursor segue opaco: por fora é
        uma string, e só este adaptador sabe que dentro há um timestamp.
        """
        since = None
        if cursor is not None:
            try:
                since = datetime.fromisoformat(cursor.opaque)
            except ValueError:
                # Cursor ilegível: pedir resync completo é o
                # comportamento correto e é o caminho que PRECISA estar
                # testado — acontece depois de qualquer mudança de
                # formato.
                return TaskSyncResult(full_resync_required=True)

        rows = await self._db.execute(
            text(
                f"""
                SELECT {_COLS}, deleted_at
                FROM core.local_tasks
                WHERE user_id = :uid
                  AND local_task_list_id = CAST(:lid AS uuid)
                  AND (CAST(:since AS timestamptz) IS NULL
                       OR updated_at > CAST(:since AS timestamptz))
                ORDER BY updated_at
                """
            ),
            {"uid": str(self._user_id), "lid": list_id, "since": since},
        )

        changed: list[Task] = []
        deleted: list[str] = []
        for row in rows.all():
            if row[-1] is not None:
                deleted.append(str(row[0]))
            else:
                changed.append(self._to_task(row))

        return TaskSyncResult(
            changed=tuple(changed),
            deleted_ids=tuple(deleted),
            next_cursor=SyncCursor(
                opaque=datetime.now(UTC).isoformat(),
                issued_at=datetime.now(UTC),
            ),
        )

    # -----------------------------------------------------------
    @staticmethod
    def _to_task(row) -> Task:
        return Task(
            id=str(row[0]),
            list_id=str(row[1]),
            title=row[2],
            notes=row[3],
            due_date=row[4],
            due_at=row[5],
            status=TaskStatus(row[6]),
            completed_at=row[7],
            priority=Priority(row[8]) if row[8] else None,
            labels=tuple(row[9] or ()),
            parent_id=str(row[10]) if row[10] else None,
            recurrence=row[11],
            estimated=(
                timedelta(minutes=row[12]) if row[12] is not None else None
            ),
            position=row[13],
            source="age" if row[14] else None,
            etag=str(row[15]),
        )


def _parse_etag(etag: str | None) -> int | None:
    if etag is None:
        return None
    try:
        return int(etag)
    except ValueError:
        raise TaskConcurrencyConflict(
            f"etag inválido para lista local: {etag!r}"
        ) from None


def _json(value) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
