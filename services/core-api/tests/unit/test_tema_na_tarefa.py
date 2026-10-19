"""O tema sobrevive da conversa até a tarefa (§5.7).

O relato que originou isto: a pessoa pediu sessões de estudo de "Funções
em Python" e depois a checklist. O Age criou 6 tarefas e 2 sessões, e
guardou a ligação e o assunto como PROSA na observação:

    "Coberto na sessão 1, hoje 16h15"

Agrupar por tema em cima disso é impossível. E `labels` — que é o campo
que existe exatamente para isso — atravessa o caminho inteiro (coluna,
adaptador local, capacidade do provedor, `_task_json`, `TaskOut` e
`lib/types.ts`) e faltava em UM lugar só: o builder que monta a tarefa
quando ela nasce de uma proposta.

Ou seja, não é campo novo. É um campo existente que caía no chão no
único ponto onde a conversa cria tarefa.
"""

from __future__ import annotations

from app.services.plan_store import (
    _build_task_item,
    _task_from_json,
    _task_json,
)


def _cria(payload: dict):
    return _build_task_item(
        op="CREATE",
        reason="motivo",
        payload={"title": "Definir e chamar funções", **payload},
        sequence=0,
        existing_tasks={},
        write_list_id="lista-padrao",
        parse=lambda s: s,
    )


class TestTemaChega:
    def test_o_tema_vira_a_primeira_etiqueta(self) -> None:
        """Sabotagem: tirar `labels=` do `Task(...)` no caso CREATE."""
        item = _cria({"labels": ["Funções em Python"]})

        assert item is not None
        assert item.after.labels == ("Funções em Python",)

    def test_tema_e_etiqueta_comum_convivem(self) -> None:
        # O tema é o PRIMEIRO; o resto segue sendo etiqueta normal, que
        # a tela já desenha. Agrupar por `labels[0]` depende desta ordem.
        item = _cria({"labels": ["Academia", "urgente"]})

        assert item.after.labels[0] == "Academia"
        assert "urgente" in item.after.labels

    def test_sobrevive_ao_json_do_plano(self) -> None:
        """A proposta vai ao banco como json e volta antes de aplicar.

        Se o tema morresse nessa ida e volta, ele existiria na proposta e
        sumiria exatamente na hora de virar tarefa de verdade.
        """
        item = _cria({"labels": ["TCC"]})

        voltou = _task_from_json(_task_json(item.after))

        assert voltou.labels == ("TCC",)


class TestSemTema:
    def test_tarefa_sem_tema_continua_valida(self) -> None:
        # Contrapeso: a maioria das tarefas não tem tema, e exigir um
        # quebraria todo o resto do app.
        item = _cria({})

        assert item.after.labels == ()

    def test_lista_vazia_nao_vira_etiqueta_fantasma(self) -> None:
        item = _cria({"labels": []})

        assert item.after.labels == ()
