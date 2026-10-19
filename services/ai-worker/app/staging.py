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
from datetime import datetime
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
    # Blocos que JÁ EXISTEM na agenda e que esta tarefa passa a cobrir.
    # Não são ids de item, são ids de evento do banco, e é por isso que
    # não podem entrar em `links`: `links` forma o grupo de APROVAÇÃO, e
    # bloco já aplicado não tem o que ser aprovado.
    event_refs: list[str] = field(default_factory=list)


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
        tema: str | None = None,
        cobre_eventos: list[str] | None = None,
        eventos_existentes: list[str] | None = None,
    ) -> str:
        """Empilha uma tarefa, opcionalmente ligada a sessões propostas.

        São DOIS parâmetros de vínculo porque são dois namespaces
        diferentes, e misturá-los era o defeito:

        - `cobre_eventos` recebe ids de itens EMPILHADOS neste mesmo
          turno (não ids de banco), que é o que faltava para a checklist
          de estudo existir: quando a pessoa pede as sessões e depois os
          to-dos, nem a tarefa nem o evento existem ainda, então
          `reserve_time_for_task` — que exige `task_id` de uma tarefa já
          gravada — não dá conta.
        - `eventos_existentes` recebe ids REAIS de evento, de
          `get_events`, para o bloco que já está na agenda. Era o caso
          que não tinha caminho nenhum: o usuário aprova os blocos num
          turno e pede os to-dos no seguinte, quando os blocos já não
          são propostas e `cobre_eventos` não alcança mais.

        Cada validador recusa o valor do outro, de propósito: id de
        banco passado em `cobre_eventos` não está em `self.items`, e id
        de item passado em `eventos_existentes` não existe na agenda.

        `tema` entra como PRIMEIRA etiqueta porque é assim que a tela
        agrupa. Etiqueta comum continua etiqueta comum, depois dele.
        """
        labels: list[str] = []
        if tema:
            labels.append(tema)

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
                "labels": labels,
            },
            sequence=self._next(),
        )
        self.items.append(item)

        # O vínculo é simétrico de propósito: `linked_group` é fechamento
        # transitivo, e com vínculo de um lado só o grupo sairia
        # diferente conforme o item por onde se começa a olhar — o que
        # faria a mesma sessão ser aplicada mais de uma vez.
        # Vai em `event_refs`, nunca em `links`: `self.link` formaria
        # grupo de aprovação com um evento que já está na agenda, e a
        # tela ofereceria aprovar e reverter o que já é fato.
        for existente in eventos_existentes or []:
            if str(existente) not in item.event_refs:
                item.event_refs.append(str(existente))

        for alvo in cobre_eventos or []:
            irmao = next(
                (i for i in self.items if str(i.id) == str(alvo)), None
            )
            # Id inventado, ou apontando para uma tarefa em vez de uma
            # sessão, é descartado em silêncio: vínculo pendurado no
            # vazio é pior que vínculo nenhum.
            if irmao is not None and irmao.target == "event":
                self.link(str(item.id), str(alvo))
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

    def stage_task_link(
        self,
        *,
        task_id: str,
        eventos_existentes: list[str],
        reason: str,
    ) -> str:
        """Liga uma tarefa que JÁ EXISTE a blocos que JÁ EXISTEM (§5.7.1).

        Operação própria, e não um `UPDATE` com vínculo pendurado, por
        um motivo medido: `_build_task_item` levanta "UPDATE de tarefa
        sem nenhuma mudança" quando o conjunto de campos alterados está
        vazio, e um update só de vínculo cairia exatamente nisso — seria
        descartado na promoção com um `log.info`, reproduzindo a falha
        silenciosa que esta mudança existe para consertar.

        Vínculo não é campo da tarefa, é relação entre duas coisas. O
        projeto já tinha tomado essa decisão para `reorder_ids`, que
        também não é campo de tarefa nenhuma.
        """
        item = StagedItem(
            id=uuid4(),
            op="LINK",
            target="task",
            reason=reason,
            payload={"task_id": task_id},
            sequence=self._next(),
            event_refs=[str(e) for e in eventos_existentes],
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

    # -----------------------------------------------------------
    # Rotina (§5.9.4)
    # -----------------------------------------------------------
    def stage_routine(
        self,
        *,
        title: str,
        rrule: str,
        first_start: str,
        duration_min: int,
        reason: str,
        flexibility: str = "flexible",
        target_per_week: int | None = None,
    ) -> str:
        """Propõe uma série recorrente.

        `target` é `routine` e não `event`: o core-api cria a linha em
        `core.routines` e materializa as ocorrências, em vez de gravar
        N eventos soltos. A diferença importa para o acompanhamento —
        eventos soltos não têm aderência, e "fui à academia essa
        semana?" não teria resposta.
        """
        item = StagedItem(
            id=uuid4(),
            op="CREATE",
            target="routine",
            reason=reason,
            payload={
                "title": title,
                "rrule": rrule,
                "first_start": first_start,
                "duration_min": duration_min,
                "flexibility": flexibility,
                "target_per_week": target_per_week,
            },
            sequence=self._next(),
        )
        self.items.append(item)
        return str(item.id)

    def drop_in_window(
        self, start: datetime, end: datetime, title_contains: str | None = None
    ) -> int:
        """Remove itens JÁ ENCENADOS neste turno, dentro da janela.

        Existe por uma sobreposição real na agenda de um usuário. Ele
        pediu um plano de estudo, achou o tempo exagerado e pediu 1h30;
        no mesmo turno o modelo chamou `propose_study_plan`, depois
        `discard_proposals`, depois `propose_study_plan` de novo. A
        resposta dizia "refiz como bloco único".

        Só que `discard_proposals` mexe em `core.plan_items`, isto é,
        no que JÁ foi promovido. O que o turno acabou de encenar está
        aqui, nesta lista, e era invisível para ele. Resultado: as duas
        versões sobreviveram no mesmo plano, cinco sessões onde devia
        haver uma, e duas delas no mesmo horário de sábado (12h15 às
        12h40 e 12h15 às 13h45).

        Devolve quantos itens saíram, para a ferramenta somar ao que o
        banco descartou e contar a verdade ao modelo.
        """
        def fora(item: StagedItem) -> bool:
            # Item sem horário (remoção, tarefa sem prazo) não pertence
            # a janela nenhuma: descartar por tempo o levaria embora
            # junto sem o modelo ter pedido isso.
            bruto = item.payload.get("start")
            if not bruto:
                return True
            try:
                quando = datetime.fromisoformat(str(bruto))
            except ValueError:
                return True
            if not (start <= quando < end):
                return True
            if title_contains:
                titulo = str(item.payload.get("title") or "")
                # `casefold` para comparar acento e caixa de forma
                # correta, igual ao filtro do core-api.
                return title_contains.casefold() not in titulo.casefold()
            return False

        antes = len(self.items)
        self.items = [i for i in self.items if fora(i)]
        return antes - len(self.items)

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
                    "event_refs": i.event_refs,
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
            vinculos = sum(1 for i in tasks if i.op == "LINK")
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
            if vinculos:
                detail.append(f"{vinculos} para ligar a um horário")
            parts.append("lista: " + ", ".join(detail))

        routines = [i for i in self.items if i.target == "routine"]
        if routines:
            parts.append(
                f"rotina: {len(routines)} nova(s)"
                if len(routines) > 1
                else "rotina: 1 nova"
            )

        pairs = sum(1 for i in self.items if i.links) // 2
        if pairs:
            parts.append(f"{pairs} par(es) ligado(s)")

        return " · ".join(parts) or "nenhuma mudança"
