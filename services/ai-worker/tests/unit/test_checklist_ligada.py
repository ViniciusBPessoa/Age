"""A checklist que sabe a que hora pertence (§5.7.1).

O relato: a pessoa pediu 2 sessões de "Funções em Python" e depois uma
checklist dos tópicos. O Age criou as 6 tarefas e as 2 sessões e **não
ligou nada** — guardou a associação como prosa na observação ("Coberto
na sessão 1, hoje 16h15"), e `task_event_links` ficou vazia.

Não era falta de instrução. Era falta de ONDE: `create_task` não tinha
parâmetro para apontar um evento, e `reserve_time_for_task` — a única
ferramenta que liga — exige `task_id` de tarefa já gravada e cria UM
evento novo por tarefa. No caso real as sessões ainda eram propostas, e
5 tarefas dividiam 2 sessões. A prosa era a única saída honesta.
"""

from __future__ import annotations

import os
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x@localhost/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("MASTER_KEY", "k" * 48)
os.environ.setdefault("SERVICE_JWT_SECRET", "s" * 48)

from app.staging import StagingBuilder  # noqa: E402

TZ = "-03:00"


def _builder() -> StagingBuilder:
    return StagingBuilder(user_id=uuid4(), conversation_id=uuid4())


def _sessao(b: StagingBuilder, dia: int) -> str:
    return b.stage_create(
        title=f"Estudo: Funções em Python ({dia})",
        start=f"2026-10-{dia:02d}T16:15:00{TZ}",
        end=f"2026-10-{dia:02d}T17:15:00{TZ}",
        reason="sessão de estudo",
    )


def _item(b: StagingBuilder, item_id: str):
    return next(i for i in b.items if str(i.id) == str(item_id))


class TestLigaNaCriacao:
    def test_tarefa_nova_aponta_para_a_sessao(self) -> None:
        """Sabotagem: `create_task` ignorar `cobre_eventos`."""
        b = _builder()
        sessao = _sessao(b, 19)
        tarefa = b.stage_task_create(
            title="Definir e chamar funções",
            reason="tópico da sessão 1",
            cobre_eventos=[sessao],
        )

        assert sessao in _item(b, tarefa).links

    def test_vinculo_e_simetrico(self) -> None:
        """Os dois lados se apontam.

        Não é preciosismo: `linked_group` é fechamento transitivo, e com
        vínculo de um lado só o grupo sairia diferente conforme o item
        por onde se começa — o que faria a mesma sessão ser aplicada
        mais de uma vez.
        """
        b = _builder()
        sessao = _sessao(b, 19)
        tarefa = b.stage_task_create(
            title="Escopo de variáveis",
            reason="tópico",
            cobre_eventos=[sessao],
        )

        assert tarefa in _item(b, sessao).links

    def test_cinco_tarefas_para_duas_sessoes(self) -> None:
        """O caso real que originou tudo."""
        b = _builder()
        s1, s2 = _sessao(b, 19), _sessao(b, 20)
        for titulo, sessoes in [
            ("Definir e chamar", [s1]),
            ("Escopo", [s1]),
            ("Default e nomeados", [s2]),
            ("Lambda", [s2]),
            ("Exercícios", [s1, s2]),
        ]:
            b.stage_task_create(
                title=titulo, reason="tópico", cobre_eventos=sessoes
            )

        assert len(_item(b, s1).links) == 3
        assert len(_item(b, s2).links) == 3

    def test_topico_que_ficou_de_fora_nao_inventa_vinculo(self) -> None:
        # "*args e **kwargs ficou fora das 2h": entra na lista SEM
        # horário. Inventar um vínculo aqui seria mentir sobre a agenda.
        b = _builder()
        _sessao(b, 19)
        tarefa = b.stage_task_create(
            title="*args e **kwargs", reason="não coube nas 2h"
        )

        assert _item(b, tarefa).links == []


class TestVinculoInvalido:
    def test_id_inventado_e_descartado(self) -> None:
        """Sabotagem: tirar a guarda de existência.

        O modelo pode alucinar um id. Vínculo pendurado no vazio é pior
        que vínculo nenhum.
        """
        b = _builder()
        tarefa = b.stage_task_create(
            title="Tópico", reason="x", cobre_eventos=[str(uuid4())]
        )

        assert _item(b, tarefa).links == []

    def test_nao_liga_tarefa_com_tarefa(self) -> None:
        # Contrapeso: `cobre_eventos` é sobre HORÁRIO. Ligar duas tarefas
        # criaria um grupo que o `_persist_task_event_links` não sabe
        # gravar, e que arrastaria itens sem relação na aprovação.
        b = _builder()
        outra = b.stage_task_create(title="Outra", reason="x")
        tarefa = b.stage_task_create(
            title="Tópico", reason="x", cobre_eventos=[outra]
        )

        assert _item(b, tarefa).links == []


class TestTema:
    def test_tema_vira_primeira_etiqueta(self) -> None:
        """Sabotagem: não montar `labels` a partir de `tema`."""
        b = _builder()
        tarefa = b.stage_task_create(
            title="Definir e chamar",
            reason="x",
            tema="Funções em Python",
        )

        assert _item(b, tarefa).payload["labels"] == ["Funções em Python"]

    def test_tema_serve_para_qualquer_area(self) -> None:
        # Não é só estudo: trabalho, academia, qualquer coisa da vida.
        b = _builder()
        tarefa = b.stage_task_create(
            title="Treino de perna", reason="x", tema="Academia"
        )

        assert _item(b, tarefa).payload["labels"] == ["Academia"]

    def test_sem_tema_nao_inventa_etiqueta(self) -> None:
        b = _builder()
        tarefa = b.stage_task_create(title="Comprar pão", reason="x")

        assert _item(b, tarefa).payload["labels"] == []
