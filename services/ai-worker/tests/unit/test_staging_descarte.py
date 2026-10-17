"""Descartar o que o PRÓPRIO turno acabou de encenar (§5.2).

Relato, com a agenda na mão: "tem duas coisas marcadas no mesmo local,
acho que isso gerou um erro".

Tinha. A sequência real do turno, lida no log do worker:

    POST /internal/study/plan          <- versão em 4 dias
    POST /internal/calendar/discard-proposals  (3x)
    POST /internal/study/plan          <- versão de 1h30

O modelo FEZ a coisa certa: chamou `discard_proposals` entre as duas
propostas. Só que aquela rota mexe em `core.plan_items`, isto é, no que
já foi promovido, e o que o turno acabou de encenar vive no
`StagingBuilder`, invisível para ela.

As duas versões sobreviveram no mesmo plano: 5 sessões onde devia haver
1, e duas no mesmo horário de sábado (12h15 às 12h40 e 12h15 às 13h45).
A resposta na tela dizia "refiz como bloco único".

Isto não era falha de prompt. Era um buraco: não existia como tirar
item do staging, só como somar.
"""

from __future__ import annotations

import os
from datetime import datetime
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x@localhost/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("MASTER_KEY", "k" * 48)
os.environ.setdefault("SERVICE_JWT_SECRET", "s" * 48)

from app.staging import StagingBuilder  # noqa: E402

TZ = "-03:00"


def _builder() -> StagingBuilder:
    return StagingBuilder(user_id=uuid4(), conversation_id=uuid4())


def _encenar(b: StagingBuilder, titulo: str, dia: int, hora: str, fim: str) -> None:
    b.stage_create(
        title=titulo,
        start=f"2026-10-{dia:02d}T{hora}:00{TZ}",
        end=f"2026-10-{dia:02d}T{fim}:00{TZ}",
        reason="sessão de estudo",
    )


def _janela(d1: int, d2: int) -> tuple[datetime, datetime]:
    return (
        datetime.fromisoformat(f"2026-10-{d1:02d}T00:00:00{TZ}"),
        datetime.fromisoformat(f"2026-10-{d2:02d}T23:59:00{TZ}"),
    )


class TestDescarteDentroDoTurno:
    def test_remove_as_sessoes_do_plano_antigo(self) -> None:
        """O caso do relato, reproduzido.

        Quatro sessões espalhadas, depois o bloco único. Sem o
        descarte, sobrariam as cinco.
        """
        b = _builder()
        _encenar(b, "Funções em Python: definir", 17, "12:15", "12:40")
        _encenar(b, "Funções em Python: escopo", 18, "08:15", "08:35")
        _encenar(b, "Funções em Python: default", 19, "08:15", "08:40")
        _encenar(b, "Funções em Python: lambda", 20, "08:15", "08:35")

        saiu = b.drop_in_window(*_janela(17, 20))

        assert saiu == 4
        assert b.is_empty

    def test_poupa_proposta_de_outra_materia(self) -> None:
        """`title_contains` é o que evita levar junto o que a pessoa não
        pediu para mexer. Ela tinha blocos de Cálculo II pendentes."""
        b = _builder()
        _encenar(b, "Funções em Python: definir", 17, "12:15", "12:40")
        _encenar(b, "Cálculo II: integrais", 17, "14:00", "15:30")

        saiu = b.drop_in_window(*_janela(17, 17), "Funções em Python")

        assert saiu == 1
        assert len(b.items) == 1
        assert "Cálculo" in b.items[0].payload["title"]

    def test_poupa_o_que_esta_fora_da_janela(self) -> None:
        b = _builder()
        _encenar(b, "Funções em Python: definir", 17, "12:15", "12:40")
        _encenar(b, "Funções em Python: lambda", 25, "08:15", "08:35")

        saiu = b.drop_in_window(*_janela(17, 18))

        assert saiu == 1
        assert len(b.items) == 1

    def test_nao_leva_item_sem_horario(self) -> None:
        """Remoção de evento e tarefa sem prazo não têm `start`.

        Descartar por tempo o que não tem tempo levaria embora, de
        carona, a remoção que a pessoa acabou de pedir. Foi o caso dela
        no mesmo dia: pediu para esvaziar a agenda, e havia um
        `propose_delete` pendente.
        """
        b = _builder()
        b.stage_delete(event_id=str(uuid4()), reason="pedido de remoção")
        _encenar(b, "Funções em Python: definir", 17, "12:15", "12:40")

        saiu = b.drop_in_window(*_janela(17, 17))

        assert saiu == 1
        assert len(b.items) == 1
        assert b.items[0].op == "DELETE"

    def test_data_malformada_nao_derruba_o_turno(self) -> None:
        """O `start` vem do modelo. Um valor inválido deve deixar o item
        em paz, não estourar no meio do turno."""
        b = _builder()
        b.stage_create(
            title="Funções em Python",
            start="amanhã às 12h",
            end="amanhã às 13h30",
            reason="sessão",
        )

        saiu = b.drop_in_window(*_janela(17, 20))

        assert saiu == 0
        assert len(b.items) == 1

    def test_sem_nada_encenado_devolve_zero(self) -> None:
        # O caso comum: a pessoa corrige algo já promovido, e o staging
        # deste turno está vazio.
        assert _builder().drop_in_window(*_janela(17, 20)) == 0
