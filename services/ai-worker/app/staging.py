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

    @property
    def is_empty(self) -> bool:
        return not self.items

    def to_json(self) -> str:
        return json.dumps(
            [
                {
                    "id": str(i.id),
                    "op": i.op,
                    "reason": i.reason,
                    "payload": i.payload,
                    "sequence": i.sequence,
                }
                for i in self.items
            ],
            ensure_ascii=False,
            default=str,
        )

    def summary(self) -> str:
        """Resumo curto para o cartão do plano."""
        creates = sum(1 for i in self.items if i.op == "CREATE")
        moves = sum(1 for i in self.items if i.op == "MOVE")
        deletes = sum(1 for i in self.items if i.op == "DELETE")

        parts = []
        if creates:
            parts.append(f"{creates} para criar")
        if moves:
            parts.append(f"{moves} para mover")
        if deletes:
            parts.append(f"{deletes} para remover")
        return ", ".join(parts) or "nenhuma mudança"
