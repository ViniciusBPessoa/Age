"""Porta `TaskProvider` — o contrato de todo provedor de lista.

ARQUITETURA.md §3.3. Tarefa e evento **não** são a mesma coisa, e
unificá-los numa entidade só é um erro que as APIs reais expõem no
primeiro dia:

| | Evento | Tarefa |
|---|---|---|
| ocupa um intervalo | sim, é a essência | não necessariamente |
| tem estado de conclusão | não | sim, é a essência |
| pode não ter hora | não faz sentido | normal |

Daí uma porta própria, com entidade própria, em vez de um `Event` com
campo `is_task`.

**A decisão de projeto mais importante deste arquivo** está em §3.3.1:
a porta foi desenhada a partir do provedor MAIS RICO (Todoist), não do
mais provável (Google Tasks). O Google Tasks não guarda hora no prazo,
nem prioridade, nem duração estimada, nem suporta sync incremental — é
o mais pobre dos três reais. Se a porta nascesse dele, `due_at`,
`priority` e `estimated` não existiriam, e acrescentá-los depois
quebraria todos os adaptadores de uma vez.

A contrapartida é que o adaptador pobre precisa declarar o que não faz
(`TaskCapabilities`) e o Age preserva o campo na sombra local (§3.3.2).
Degradação declarada, nunca silenciosa.

Nada neste arquivo importa SDK, httpx ou SQLAlchemy — invariante de
§10.2.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

# Sync é o mesmo conceito nas duas portas — cursor opaco que só o
# adaptador interpreta (§3.2.2). Reusar os tipos evita duas definições
# que divergem com o tempo; `SyncResult` de tarefa carrega `Task`, por
# isso tem forma própria abaixo.
from app.ports.calendar import SyncCapability, SyncCursor


# ---------------------------------------------------------------
# Entidades
# ---------------------------------------------------------------
@dataclass(frozen=True)
class TaskListRef:
    """Uma lista dentro de uma conexão."""

    id: str
    name: str
    is_default: bool = False
    writable: bool = True
    color: str | None = None


class TaskStatus(StrEnum):
    OPEN = "open"
    DONE = "done"


class Priority(StrEnum):
    """Prioridade nomeada, não número solto.

    A escala do Todoist é 1–4 e a do Microsoft To Do é
    low/normal/high. Guardar o número cru de um provedor faria a
    tradução acontecer em todo lugar que lê o campo; um enum do Age
    deixa a tradução dentro do adaptador, que é onde ela pertence.
    """

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"

    @property
    def rank(self) -> int:
        """Ordem numérica, para ordenar e comparar."""
        return _PRIORITY_RANK[self]


_PRIORITY_RANK = {
    Priority.LOW: 1,
    Priority.NORMAL: 2,
    Priority.HIGH: 3,
    Priority.URGENT: 4,
}


@dataclass(frozen=True)
class Task:
    """Algo a fazer, com ou sem hora marcada.

    Sobre `due_date` vs. `due_at`: a distinção é **do domínio**, não um
    detalhe de provedor. "Entregar até sexta" e "ligar sexta às 15h"
    são compromissos diferentes, e achatar os dois num campo só perde
    informação que o usuário digitou. Quando há `due_at`, `due_date` é
    derivado dele e mantido coerente por `__post_init__` — assim quem
    só sabe lidar com data (o Google Tasks, a UI de lista) nunca precisa
    de um `if`.
    """

    id: str | None
    list_id: str
    title: str
    notes: str | None = None

    due_date: date | None = None
    due_at: datetime | None = None  # tz-aware; None se só data

    status: TaskStatus = TaskStatus.OPEN
    completed_at: datetime | None = None

    # Opcionais — cada provedor declara se suporta (§3.3.2)
    priority: Priority | None = None
    labels: tuple[str, ...] = ()
    parent_id: str | None = None  # subtarefa
    recurrence: str | None = None  # RRULE
    estimated: timedelta | None = None

    # Posição na lista, para `REORDER` (§5.7)
    position: int | None = None

    # Metadados do Age, nunca enviados ao provedor
    source: str | None = None
    etag: str | None = None

    # Campos que o provedor NÃO guardou nesta tarefa (§3.3.2).
    #
    # Vive na entidade devolvida, e não num registro paralelo dentro do
    # adaptador, por uma razão aprendida errando: a primeira versão
    # guardava num dicionário `{task_id: campos}`, e como o id só existe
    # DEPOIS do insert, a chave era sempre vazia no `create_task` — o
    # campo era descartado e **nada era registrado**. O teste de
    # contrato pegou, mas o bug era estrutural: um canal de sinalização
    # separado do dado pode se perder sem que nada quebre.
    #
    # Como parte do retorno, quem chama não tem como não ver.
    dropped_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.due_at is not None:
            if self.due_at.tzinfo is None:
                raise ValueError(
                    f"Task '{self.title}': due_at precisa de timezone. "
                    "Datetime naive é a causa raiz de quase todo bug de "
                    "prazo."
                )
            # Coerência entre os dois campos, resolvida aqui e não em
            # cada leitor. `object.__setattr__` porque o dataclass é
            # frozen e este é o único ponto legítimo de normalização.
            expected = self.due_at.date()
            if self.due_date != expected:
                object.__setattr__(self, "due_date", expected)

        if self.status == TaskStatus.DONE and self.completed_at is None:
            raise ValueError(
                f"Task '{self.title}': status DONE exige completed_at — "
                "sem ele não há como relatar o que foi feito quando (§5.9.9)"
            )

        if self.estimated is not None and self.estimated <= timedelta():
            raise ValueError(
                f"Task '{self.title}': estimated precisa ser positivo"
            )

    @property
    def is_done(self) -> bool:
        return self.status == TaskStatus.DONE

    @property
    def has_due_time(self) -> bool:
        """Tem hora, não só data. Decide se o `due` virou compromisso."""
        return self.due_at is not None

    @property
    def estimated_min(self) -> int | None:
        if self.estimated is None:
            return None
        return int(self.estimated.total_seconds() // 60)

    @property
    def was_degraded(self) -> bool:
        return bool(self.dropped_fields)

    def is_overdue(self, now: datetime) -> bool:
        """Prazo vencido.

        Tarefa só com data vence no FIM do dia, não às 00:00 — "até
        sexta" significa sexta inteira. Tratar como 00:00 marcaria como
        atrasado tudo que ainda dá tempo de fazer, que é o erro que faz
        o usuário desconfiar da lista.
        """
        if self.is_done:
            return False
        if self.due_at is not None:
            return now > self.due_at
        if self.due_date is not None:
            return now.date() > self.due_date
        return False


# ---------------------------------------------------------------
# Sincronização
# ---------------------------------------------------------------
@dataclass(frozen=True)
class TaskSyncResult:
    changed: tuple[Task, ...] = ()
    deleted_ids: tuple[str, ...] = ()
    next_cursor: SyncCursor | None = None
    full_resync_required: bool = False


# ---------------------------------------------------------------
# Capacidades
# ---------------------------------------------------------------
@dataclass(frozen=True)
class TaskCapabilities:
    """O que o provedor realmente faz. Declarar é prometer (§10.1).

    A suíte de contrato verifica cada bandeira contra o comportamento:
    quem declara `supports_due_time` tem de fazer roundtrip com hora, e
    quem não declara tem de levantar `NotSupported` ou sinalizar
    degradação — nunca aceitar e perder o dado.
    """

    supports_due_time: bool
    supports_estimated_duration: bool
    supports_priority: bool
    supports_labels: bool
    supports_subtasks: bool
    supports_recurrence: bool
    supports_notes: bool = True
    supports_reorder: bool = True
    supports_etag_concurrency: bool = False
    sync: SyncCapability = SyncCapability.POLL_ONLY
    max_title_length: int | None = None  # Todoist: 500

    @property
    def supports_incremental_sync(self) -> bool:
        return self.sync in (SyncCapability.INCREMENTAL, SyncCapability.PUSH)

    def unsupported_fields(self, task: Task) -> tuple[str, ...]:
        """Campos preenchidos que este provedor não guarda (§3.3.2).

        É o que alimenta a sombra local e o aviso ao usuário. Calculado
        na porta, não em cada adaptador, para que nenhum adaptador possa
        "esquecer" de reportar uma perda.
        """
        lost: list[str] = []
        if task.due_at is not None and not self.supports_due_time:
            lost.append("due_at")
        if task.estimated is not None and not self.supports_estimated_duration:
            lost.append("estimated")
        if task.priority is not None and not self.supports_priority:
            lost.append("priority")
        if task.labels and not self.supports_labels:
            lost.append("labels")
        if task.recurrence is not None and not self.supports_recurrence:
            lost.append("recurrence")
        if task.notes is not None and not self.supports_notes:
            lost.append("notes")
        return tuple(lost)


def degrade_for(task: Task, caps: TaskCapabilities) -> Task:
    """Remove o que o provedor não guarda e REGISTRA o que saiu (§3.3.2).

    Função da porta, não de cada adaptador, porque é a regra que impede
    perda invisível de dado — e deixá-la em cada adaptador é dar a todos
    eles a chance de esquecer de reportar.

    `due_date` é preservado sempre: todo provedor guarda data, é o
    mínimo comum de §3.3.1. O que se perde é a hora.
    """
    lost = caps.unsupported_fields(task)
    if not lost:
        return task

    from dataclasses import replace

    changes: dict[str, object] = {"dropped_fields": lost}
    if "due_at" in lost:
        # Comportamento literal da API do Google Tasks: "the time
        # portion of the timestamp is discarded". O dia sobrevive —
        # `due_date` já foi normalizado a partir de `due_at` na
        # entidade, então a data continua correta.
        changes["due_at"] = None
    if "estimated" in lost:
        changes["estimated"] = None
    if "priority" in lost:
        changes["priority"] = None
    if "labels" in lost:
        changes["labels"] = ()
    if "recurrence" in lost:
        changes["recurrence"] = None
    if "notes" in lost:
        changes["notes"] = None

    return replace(task, **changes)  # type: ignore[arg-type]


def describe_degradation(
    provider_name: str, dropped: tuple[str, ...]
) -> str:
    """O aviso que o usuário lê, uma vez, na tela de conexão (§3.3.2).

    Texto na porta e não na UI porque o conteúdo depende do que o
    adaptador declarou, e porque é melhor que a frase exista num lugar
    só — ela aparece na conexão, no cartão do plano e na resposta do
    assistente (§5.9.5).
    """
    if not dropped:
        return ""

    nomes = {
        "due_at": "hora do prazo",
        "estimated": "duração estimada",
        "priority": "prioridade",
        "labels": "etiquetas",
        "recurrence": "recorrência",
        "notes": "anotações",
    }
    legiveis = [nomes.get(field, field) for field in dropped]
    if len(legiveis) == 1:
        lista = legiveis[0]
    else:
        lista = ", ".join(legiveis[:-1]) + f" nem {legiveis[-1]}"

    return (
        f"O {provider_name} não guarda {lista}. O Age mantém essa "
        "informação por aqui; se você abrir a tarefa no app do provedor, "
        "vai ver só o que ele suporta."
    )


# ---------------------------------------------------------------
# Erros
# ---------------------------------------------------------------
class TaskError(Exception):
    code = "task_error"
    retryable = False

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider


class TaskNotSupported(TaskError):
    code = "task_not_supported"


class TaskAuthenticationError(TaskError):
    code = "task_auth_failed"


class TaskRateLimitError(TaskError):
    code = "task_rate_limited"
    retryable = True


class TaskProviderUnavailable(TaskError):
    code = "task_unavailable"
    retryable = True


class TaskNotFound(TaskError):
    code = "task_not_found"


class TaskListNotFound(TaskError):
    code = "task_list_not_found"


class TaskConcurrencyConflict(TaskError):
    """A tarefa mudou desde a leitura (etag divergente).

    Mesmo papel que em calendário: o `PlanExecutor` revalida antes de
    aplicar (§5.8). Aprovar um plano e descobrir que a lista mudou é
    melhor que sobrescrever a mudança de alguém em silêncio.
    """

    code = "task_concurrency_conflict"


# ---------------------------------------------------------------
# A porta
# ---------------------------------------------------------------
class TaskProvider(ABC):
    key: str
    display_name: str

    @property
    @abstractmethod
    def capabilities(self) -> TaskCapabilities: ...

    @abstractmethod
    async def list_task_lists(self) -> list[TaskListRef]: ...

    @abstractmethod
    async def list_tasks(
        self,
        list_ids: list[str],
        *,
        include_done: bool = False,
        due_before: date | None = None,
    ) -> list[Task]:
        """Tarefas das listas pedidas.

        Recebe LISTA de listas pelo mesmo motivo que o calendário recebe
        lista de calendários: o usuário pode ter a lista nativa e uma
        externa ao mesmo tempo, e "o que tenho pra fazer" (§5.9.5) tem
        de cruzar as duas.

        `include_done=False` por padrão porque a pergunta normal é sobre
        o que falta; o relatório (§5.9.9) é quem pede as concluídas.
        """

    @abstractmethod
    async def create_task(self, task: Task) -> Task: ...

    @abstractmethod
    async def update_task(
        self, task: Task, expected_etag: str | None = None
    ) -> Task: ...

    @abstractmethod
    async def complete_task(self, list_id: str, task_id: str) -> Task:
        """Marca como feita.

        Caminho próprio e não `update_task(status=DONE)` porque vários
        provedores têm endpoint dedicado, e porque concluir é a operação
        que o produto mais faz — merece ser explícita na porta.
        """

    @abstractmethod
    async def delete_task(self, list_id: str, task_id: str) -> None:
        """Remove. Idempotente: apagar o que não existe não é erro."""

    async def reorder(self, list_id: str, ordered_ids: list[str]) -> None:
        """Reposiciona tarefas na lista (§5.7, op `REORDER`).

        Default levanta `NotSupported`: quem reordena sobrescreve.
        """
        raise TaskNotSupported(f"{self.key} não suporta reordenação")

    @abstractmethod
    async def sync(
        self, list_id: str, cursor: SyncCursor | None
    ) -> TaskSyncResult: ...

    async def aclose(self) -> None:
        return None
