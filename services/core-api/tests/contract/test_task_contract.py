"""Suíte de contrato de `TaskProvider`.

ARQUITETURA.md §10.1. A mesma suíte roda contra todo adaptador, e um
adaptador novo só é aceito se passar aqui — é assim que "pode trocar o
provedor" deixa de ser promessa e passa a ser fato verificado.

Roda contra dois provedores de propósito:

- `InMemoryTasks` — capacidades plenas
- `PoorInMemoryTasks` — as limitações exatas do Google Tasks (§3.3.1)

O segundo é o que dá valor à suíte. Testar só o provedor rico provaria
que o caminho fácil funciona; a degradação (§3.3.2) é o caminho que
falha em silêncio, porque um campo que desaparece não levanta exceção.

`LocalTasks` precisa de banco e é coberto pelos testes de integração e
pelo smoke do M4.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

# Importar registra cada adaptador no registry (§3.3).
from app.adapters.tasks import in_memory, local  # noqa: F401
from app.adapters.tasks.registry import (
    available_task_providers,
    get_tasks_class,
    is_registered,
)
from app.ports.calendar import SyncCursor
from app.ports.tasks import (
    Priority,
    Task,
    TaskNotFound,
    TaskNotSupported,
    TaskProvider,
    TaskStatus,
)

SP = ZoneInfo("America/Sao_Paulo")


def offline_providers() -> list[TaskProvider]:
    """Provedores que rodam sem rede nem banco."""
    from app.adapters.tasks.in_memory import InMemoryTasks, PoorInMemoryTasks

    return [InMemoryTasks(), PoorInMemoryTasks()]


def sample_task(
    *,
    list_id: str = "default",
    title: str = "Revisar o capítulo 2",
    due_date: date | None = None,
) -> Task:
    return Task(
        id=None,
        list_id=list_id,
        title=title,
        due_date=due_date or date(2026, 10, 10),
    )


# ---------------------------------------------------------------
# Registry
# ---------------------------------------------------------------
def test_expected_providers_registered() -> None:
    for key in ("local", "in_memory", "in_memory_poor"):
        assert is_registered(key), f"provedor '{key}' não registrado"


def test_registry_lists_providers() -> None:
    keys = {p["key"] for p in available_task_providers()}
    assert "local" in keys


def test_unknown_provider_raises() -> None:
    with pytest.raises(TaskNotSupported):
        get_tasks_class("todoist_que_nao_existe_ainda")


# ---------------------------------------------------------------
# O contrato
# ---------------------------------------------------------------
@pytest.mark.parametrize("provider", offline_providers(), ids=lambda p: p.key)
class TestTaskProviderContract:
    async def test_create_then_read_roundtrip(
        self, provider: TaskProvider
    ) -> None:
        created = await provider.create_task(sample_task())
        assert created.id is not None

        found = await provider.list_tasks([created.list_id])
        assert any(t.id == created.id for t in found)

    async def test_default_list_exists(self, provider: TaskProvider) -> None:
        """Todo provedor tem onde escrever sem o usuário escolher."""
        lists = await provider.list_task_lists()
        assert lists, "nenhuma lista disponível"
        assert any(entry.is_default for entry in lists)

    async def test_complete_sets_status_and_timestamp(
        self, provider: TaskProvider
    ) -> None:
        created = await provider.create_task(sample_task())
        assert created.id is not None

        done = await provider.complete_task(created.list_id, created.id)
        assert done.status == TaskStatus.DONE
        # Sem `completed_at` não há relatório de §5.9.9: a invariante é
        # imposta pela porta, e aqui é verificada no adaptador.
        assert done.completed_at is not None

    async def test_complete_is_idempotent_and_keeps_first_timestamp(
        self, provider: TaskProvider
    ) -> None:
        """Duplo-clique no botão é o caso normal, não o excepcional.

        Sobrescrever `completed_at` faria o relatório mentir sobre
        quando a coisa aconteceu.
        """
        created = await provider.create_task(sample_task())
        assert created.id is not None

        first = await provider.complete_task(created.list_id, created.id)
        second = await provider.complete_task(created.list_id, created.id)
        assert first.completed_at == second.completed_at

    async def test_open_tasks_exclude_done_by_default(
        self, provider: TaskProvider
    ) -> None:
        created = await provider.create_task(sample_task(title="Pagar IPVA"))
        assert created.id is not None
        await provider.complete_task(created.list_id, created.id)

        open_only = await provider.list_tasks([created.list_id])
        assert all(t.id != created.id for t in open_only)

        with_done = await provider.list_tasks(
            [created.list_id], include_done=True
        )
        assert any(t.id == created.id for t in with_done)

    async def test_due_date_without_time_roundtrips(
        self, provider: TaskProvider
    ) -> None:
        """Todo provedor guarda data. É o mínimo comum (§3.3.1)."""
        deadline = date(2026, 11, 20)
        created = await provider.create_task(
            sample_task(title="Entregar o TCC", due_date=deadline)
        )
        assert created.due_date == deadline

    async def test_due_time_respects_capability(
        self, provider: TaskProvider
    ) -> None:
        """A regra central de §3.3.2, nos dois sentidos.

        Suporta → roundtrip com hora.
        Não suporta → a DATA sobrevive, a hora não, e o adaptador
        SINALIZA a perda em vez de sumir com o dado.
        """
        at = datetime(2026, 10, 10, 15, 0, tzinfo=SP)
        created = await provider.create_task(
            Task(id=None, list_id="default", title="Ligar pro dentista",
                 due_at=at)
        )

        if provider.capabilities.supports_due_time:
            assert created.due_at == at
        else:
            assert created.due_at is None, (
                "provedor sem suporte a hora não deve inventar uma"
            )
            # O que o Google Tasks faz: guarda o dia, descarta a hora.
            assert created.due_date == at.date()
            # E o que o Age exige dele: dizer o que descartou.
            assert "due_at" in created.dropped_fields
            assert created.was_degraded

    async def test_priority_respects_capability(
        self, provider: TaskProvider
    ) -> None:
        created = await provider.create_task(
            Task(
                id=None,
                list_id="default",
                title="Responder orientador",
                due_date=date(2026, 10, 9),
                priority=Priority.URGENT,
            )
        )
        if provider.capabilities.supports_priority:
            assert created.priority == Priority.URGENT
        else:
            assert created.priority is None
            assert "priority" in created.dropped_fields

    async def test_estimated_duration_respects_capability(
        self, provider: TaskProvider
    ) -> None:
        """A duração é o que permite responder "cabe?" em §5.9.5.

        Quando o provedor não a guarda, perdê-la calada tiraria do
        produto a resposta que o diferencia de um to-do qualquer.
        """
        created = await provider.create_task(
            Task(
                id=None,
                list_id="default",
                title="Organizar as fotos",
                due_date=date(2026, 10, 15),
                estimated=timedelta(hours=2),
            )
        )
        if provider.capabilities.supports_estimated_duration:
            assert created.estimated == timedelta(hours=2)
            assert created.estimated_min == 120
        else:
            assert created.estimated is None
            assert "estimated" in created.dropped_fields

    async def test_labels_respect_capability(
        self, provider: TaskProvider
    ) -> None:
        created = await provider.create_task(
            Task(
                id=None,
                list_id="default",
                title="Comprar presente",
                labels=("pessoal", "urgente"),
            )
        )
        if provider.capabilities.supports_labels:
            assert set(created.labels) == {"pessoal", "urgente"}
        else:
            assert created.labels == ()
            assert "labels" in created.dropped_fields

    async def test_declared_capabilities_are_truthful(
        self, provider: TaskProvider
    ) -> None:
        """Declarar é prometer — a mesma invariante do calendário.

        Esta é a asserção que impede uma bandeira otimista de passar: um
        adaptador que diz suportar reordenação tem de reordenar.
        """
        caps = provider.capabilities
        created = await provider.create_task(sample_task())
        assert created.id is not None

        if caps.supports_reorder:
            await provider.reorder(created.list_id, [created.id])
        else:
            with pytest.raises(TaskNotSupported):
                await provider.reorder(created.list_id, [created.id])

    async def test_update_changes_fields(
        self, provider: TaskProvider
    ) -> None:
        from dataclasses import replace

        created = await provider.create_task(sample_task())
        updated = await provider.update_task(
            replace(created, title="Revisar o capítulo 2 (revisado)")
        )
        assert updated.title == "Revisar o capítulo 2 (revisado)"

    async def test_update_unknown_task_raises(
        self, provider: TaskProvider
    ) -> None:
        from dataclasses import replace

        ghost = replace(
            sample_task(), id="00000000-0000-0000-0000-000000000000"
        )
        with pytest.raises(TaskNotFound):
            await provider.update_task(ghost)

    async def test_delete_is_idempotent(
        self, provider: TaskProvider
    ) -> None:
        """Apagar o que não existe não é erro.

        Importa para o undo (§5.8): a reversão pode rodar duas vezes se
        a primeira falhou no meio, e não deve explodir na segunda.
        """
        created = await provider.create_task(sample_task())
        assert created.id is not None

        await provider.delete_task(created.list_id, created.id)
        await provider.delete_task(created.list_id, created.id)  # de novo

        remaining = await provider.list_tasks([created.list_id])
        assert all(t.id != created.id for t in remaining)

    async def test_sync_cursor_roundtrip_or_not_supported(
        self, provider: TaskProvider
    ) -> None:
        """Quem não suporta levanta `NotSupported`, não finge.

        O Google Tasks declara `supports_incremental_sync=False`. Se em
        vez de levantar ele devolvesse lista vazia, o Age concluiria que
        nada mudou e pararia de sincronizar — falha silenciosa, a pior
        categoria.
        """
        await provider.create_task(sample_task())

        if not provider.capabilities.supports_incremental_sync:
            with pytest.raises(TaskNotSupported):
                await provider.sync("default", None)
            return

        first = await provider.sync("default", None)
        assert first.next_cursor is not None
        assert any(t.title == "Revisar o capítulo 2" for t in first.changed)

        # Nada mudou desde o cursor: delta vazio, não resync.
        second = await provider.sync("default", first.next_cursor)
        assert not second.full_resync_required
        assert second.changed == ()

    async def test_invalid_cursor_requests_full_resync(
        self, provider: TaskProvider
    ) -> None:
        """O caminho que acontece depois de qualquer indisponibilidade.

        Cursor ilegível tem de pedir resync, não estourar: a alternativa
        é a sincronização parar de funcionar para sempre, em silêncio.
        """
        if not provider.capabilities.supports_incremental_sync:
            pytest.skip("provedor sem sync incremental")

        result = await provider.sync(
            "default",
            SyncCursor(opaque="lixo-que-nao-parseia", issued_at=datetime.now(UTC)),
        )
        assert result.full_resync_required

    async def test_title_length_limit_is_enforced_when_declared(
        self, provider: TaskProvider
    ) -> None:
        limit = provider.capabilities.max_title_length
        if limit is None:
            pytest.skip("provedor sem limite declarado")

        with pytest.raises(TaskNotSupported):
            await provider.create_task(
                sample_task(title="x" * (limit + 1))
            )
