"""Acumulação de propostas no worker.

ARQUITETURA.md §5.2 e §2.3.2. O worker não pode escrever em `core`, e
`core.plans` é de lá. Então as propostas são acumuladas aqui em memória
durante o turno e gravadas em `ai.plan_drafts`; o `core-api` as promove
a `core.plans` quando lê (mesmo padrão de `turn_outputs`, §6.3).

Não há duplicação de regra: a validação de conflito e a aplicação ficam
no `core-api`, que é o dono da agenda. Aqui só se acumula intenção.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4


@dataclass
class StagedItem:
    id: UUID
    op: str
    reason: str
    payload: dict[str, Any]
    sequence: int
    # Qual porta recebe (§5.7). Default `event` porque todo item que
    # existia antes do M4 era de agenda — e porque um rascunho sem
    # destino explícito não deve virar tarefa por acidente.
    target: str = "event"
    # Ids de outros itens que formam unidade com este (§5.7.1).
    links: list[str] = field(default_factory=list)


@dataclass
class StagingBuilder:
    """Coleta propostas durante o laço do orquestrador.

    Os métodos `stage_*` são o que as tools de escrita chamam. Note que
    nenhum deles toca agenda, banco ou provedor: é estruturalmente
    impossível uma tool de escrita aplicar algo.
    """

    user_id: UUID
    conversation_id: UUID
    items: list[StagedItem] = field(default_factory=list)
    _sequence: int = 0

    def _next(self) -> int:
        value = self._sequence
        self._sequence += 1
        return value

    def stage_create(
        self,
        *,
        title: str,
        start: str,
        end: str,
        reason: str,
        description: str | None = None,
        flexibility: str = "flexible",
    ) -> str:
        item = StagedItem(
            id=uuid4(),
            op="CREATE",
            reason=reason,
            payload={
                "title": title,
                "start": start,
                "end": end,
                "description": description,
                "flexibility": flexibility,
            },
            sequence=self._next(),
        )
        self.items.append(item)
        return str(item.id)

    def stage_move(
        self, *, event_id: str, new_start: str, new_end: str, reason: str
    ) -> str:
        item = StagedItem(
            id=uuid4(),
            op="MOVE",
            reason=reason,
            payload={
                "event_id": event_id,
                "new_start": new_start,
                "new_end": new_end,
            },
            sequence=self._next(),
        )
        self.items.append(item)
        return str(item.id)

    def stage_delete(self, *, event_id: str, reason: str) -> str:
        item = StagedItem(
            id=uuid4(),
            op="DELETE",
            reason=reason,
            payload={"event_id": event_id},
            sequence=self._next(),
        )
        self.items.append(item)
        return str(item.id)

    def stage_recurring(
        self,
        *,
        title: str,
        first_start: str,
        duration_min: int,
        rrule: str,
        reason: str,
    ) -> str:
        item = StagedItem(
            id=uuid4(),
            op="CREATE",
            reason=reason,
            payload={
                "title": title,
                "start": first_start,
                "duration_min": duration_min,
                "rrule": rrule,
                "flexibility": "flexible",
            },
            sequence=self._next(),
        )
        self.items.append(item)
        return str(item.id)

    # -----------------------------------------------------------
    # Tarefa (§5.7)
    # -----------------------------------------------------------
    def stage_task_create(
        self,
        *,
        title: str,
        reason: str,
        notes: str | None = None,
        due_date: str | None = None,
        due_at: str | None = None,
        priority: str | None = None,
        estimated_min: int | None = None,
        list_id: str | None = None,
    ) -> str:
        item = StagedItem(
            id=uuid4(),
            op="CREATE",
            target="task",
            reason=reason,
            payload={
                "title": title,
                "notes": notes,
                "due_date": due_date,
                "due_at": due_at,
                "priority": priority,
                "estimated_min": estimated_min,
                "list_id": list_id,
            },
            sequence=self._next(),
        )
        self.items.append(item)
        return str(item.id)

    def stage_task_update(
        self,
        *,
        task_id: str,
        reason: str,
        title: str | None = None,
        due_date: str | None = None,
        due_at: str | None = None,
        priority: str | None = None,
        estimated_min: int | None = None,
        list_id: str | None = None,
    ) -> str:
        item = StagedItem(
            id=uuid4(),
            op="UPDATE",
            target="task",
            reason=reason,
            payload={
                "task_id": task_id,
                "title": title,
                "due_date": due_date,
                "due_at": due_at,
                "priority": priority,
                "estimated_min": estimated_min,
                "list_id": list_id,
            },
            sequence=self._next(),
        )
        self.items.append(item)
        return str(item.id)

    def stage_task_complete(self, *, task_id: str, reason: str) -> str:
        """Concluir — SEMPRE por pedido explícito do usuário (§5.7.3).

        A regra mais importante da organização em massa: nunca concluir
        tarefa por inferência. "Você comprou o cabo?" é pergunta, nunca
        `COMPLETE`. Concluir algo que a pessoa não fez destrói a
        confiança na lista, e numa lista em que não se confia nada é
        anotado.

        O `reason` tem de citar o pedido; o cartão mostra essa
        justificativa antes de qualquer coisa ser aplicada.
        """
        item = StagedItem(
            id=uuid4(),
            op="COMPLETE",
            target="task",
            reason=reason,
            payload={"task_id": task_id},
            sequence=self._next(),
        )
        self.items.append(item)
        return str(item.id)

    def stage_task_delete(self, *, task_id: str, reason: str) -> str:
        item = StagedItem(
            id=uuid4(),
            op="DELETE",
            target="task",
            reason=reason,
            payload={"task_id": task_id},
            sequence=self._next(),
        )
        self.items.append(item)
        return str(item.id)

    def stage_task_reorder(
        self, *, list_id: str, ordered_ids: list[str], reason: str
    ) -> str:
        item = StagedItem(
            id=uuid4(),
            op="REORDER",
            target="task",
            reason=reason,
            payload={"list_id": list_id, "ordered_ids": ordered_ids},
            sequence=self._next(),
        )
        self.items.append(item)
        return str(item.id)

    def stage_time_for_task(
        self,
        *,
        task_id: str,
        title: str,
        start: str,
        end: str,
        reason: str,
    ) -> dict[str, str]:
        """Reserva tempo para uma tarefa: DOIS itens ligados (§5.7.1).

        O vínculo é o que evita o usuário manter duas listas à mão.
        Aprovar meio par deixaria bloco na agenda sem tarefa, ou tarefa
        apontando para horário vazio, então a UI trata o par como
        unidade e o executor reverte junto.
        """
        event_id = self.stage_create(
            title=title, start=start, end=end, reason=reason
        )
        task_item_id = self.stage_task_update(
            task_id=task_id,
            due_at=start,
            reason=f"aponta para o bloco reservado em {start}",
        )
        self.link(event_id, task_item_id)
        return {"event_item_id": event_id, "task_item_id": task_item_id}

    def link(self, a: str, b: str) -> None:
        for item in self.items:
            if str(item.id) == a and b not in item.links:
                item.links.append(b)
            elif str(item.id) == b and a not in item.links:
                item.links.append(a)

    @property
    def is_empty(self) -> bool:
        return not self.items

    def to_json(self) -> str:
        return json.dumps(
            [
                {
                    "id": str(i.id),
                    "op": i.op,
                    "target": i.target,
                    "reason": i.reason,
                    "payload": i.payload,
                    "links": i.links,
                    "sequence": i.sequence,
                }
                for i in self.items
            ],
            ensure_ascii=False,
            default=str,
        )

    def summary(self) -> str:
        """Resumo curto para o cartão do plano.

        Separa agenda de lista: "agenda: 2 para criar · lista: 1 nova"
        diz ao usuário ONDE ele vai sentir a mudança, que é mais útil
        que o total — especialmente depois do M4, em que um plano pode
        mexer nas duas coisas de uma vez.
        """
        events = [i for i in self.items if i.target == "event"]
        tasks = [i for i in self.items if i.target == "task"]

        parts: list[str] = []
        if events:
            detail: list[str] = []
            creates = sum(1 for i in events if i.op == "CREATE")
            moves = sum(1 for i in events if i.op == "MOVE")
            deletes = sum(1 for i in events if i.op == "DELETE")
            if creates:
                detail.append(f"{creates} para criar")
            if moves:
                detail.append(f"{moves} para mover")
            if deletes:
                detail.append(f"{deletes} para remover")
            parts.append("agenda: " + ", ".join(detail))

        if tasks:
            detail = []
            creates = sum(1 for i in tasks if i.op == "CREATE")
            updates = sum(1 for i in tasks if i.op == "UPDATE")
            completes = sum(1 for i in tasks if i.op == "COMPLETE")
            deletes = sum(1 for i in tasks if i.op == "DELETE")
            reorders = sum(1 for i in tasks if i.op == "REORDER")
            if creates:
                detail.append(f"{creates} nova(s)")
            if updates:
                detail.append(f"{updates} para ajustar")
            if completes:
                detail.append(f"{completes} para concluir")
            if deletes:
                detail.append(f"{deletes} para remover")
            if reorders:
                detail.append("reordenar")
            parts.append("lista: " + ", ".join(detail))

        pairs = sum(1 for i in self.items if i.links) // 2
        if pairs:
            parts.append(f"{pairs} par(es) ligado(s)")

        return " · ".join(parts) or "nenhuma mudança"
