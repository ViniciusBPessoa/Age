"""M5: rotinas recorrentes, aderência e mudança externa

Revision ID: 0009_m5_routines
Revises: 0008_m4_tasks
Create Date: 2026-10-08

ARQUITETURA.md §6.1, §5.9.4 e §5.10. Três grupos:

**Rotina** (`routines`, `routine_occurrences`) — hábito com hora
marcada. A decisão de §1.2 que molda tudo aqui: **não existe hábito
flutuante.** Academia 3× por semana é uma série de eventos reais na
agenda, que ocupam tempo e entram no cálculo de carga e de conflito.
É o que o Age faz e um app de hábitos não faz.

Daí `routine_occurrences` ter uma linha POR OCORRÊNCIA, materializada,
em vez de a aderência ser derivada da RRULE na leitura: a ocorrência
registra o que *aconteceu* (feito, pulado, movido), e isso não é
função da regra de recorrência. Uma sessão movida de terça para quinta
continua sendo a ocorrência de terça — se a aderência fosse derivada,
ela apareceria como "faltou terça, fez quinta a mais".

**Mudança externa** (`external_change_log`) — §5.10. O sync detecta que
algo mudou fora do Age; isso NÃO gera notificação, porque o assistente
não inicia conversa (§1.2). Fica registrado aqui e é reconhecido na
próxima vez que o usuário abrir o chat.

A coluna `acknowledged_at` é o que torna isso reativo e não repetitivo:
sem ela, a mesma reunião seria anunciada em toda mensagem.

**Relatório** não tem tabela própria, de propósito: ele é consulta
sobre `study_sessions`, `routine_occurrences` e `local_events`. Uma
tabela de agregado precisaria ser mantida em sincronia com três fontes,
e o volume de um usuário pessoal não justifica — somar na leitura é
correto e barato aqui.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_m5_routines"
down_revision: str | None = "0008_m4_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UID = "NULLIF(current_setting('app.user_id', true), '')::uuid"


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE core.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE core.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY own_rows ON core.{table}
          USING (user_id = {_UID})
          WITH CHECK (user_id = {_UID})
        """
    )


def upgrade() -> None:
    # -----------------------------------------------------------
    # Rotina (§5.9.4)
    # -----------------------------------------------------------
    op.create_table(
        "routines",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        # RRULE (RFC 5545) sem DTSTART: o início vive em `started_at`,
        # porque é ele que o usuário pausa e retoma.
        sa.Column("rrule", sa.Text(), nullable=False),
        sa.Column("duration_min", sa.Integer(), nullable=False),
        # Rotina nasce FLEXIBLE (§5.9.4): academia é exatamente o tipo
        # de evento que o assistente pode propor mover para abrir
        # espaço. O usuário é avisado disso na criação, e pode fixar.
        sa.Column(
            "flexibility",
            sa.Text(),
            nullable=False,
            server_default="flexible",
        ),
        # Meta declarada ("3x por semana"). Serve ao painel de
        # aderência, que compara com o que de fato aconteceu — não para
        # gerar streak nem medalha (§5.9.4).
        sa.Column("target_per_week", sa.Integer(), nullable=True),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False
        ),
        # Pausar NÃO apaga: a aderência histórica continua valendo, e
        # retomar é um clique. Apagar a série perderia o histórico, que
        # é justamente o que dá valor ao painel.
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["core.users.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "flexibility IN ('fixed', 'semi_flexible', 'flexible')",
            name="routines_flexibility_valid",
        ),
        sa.CheckConstraint(
            "duration_min > 0 AND duration_min <= 1440",
            name="routines_duration_valid",
        ),
        sa.CheckConstraint(
            "target_per_week IS NULL OR "
            "(target_per_week > 0 AND target_per_week <= 21)",
            name="routines_target_valid",
        ),
        schema="core",
    )
    op.execute(
        """
        CREATE INDEX routines_active_idx
          ON core.routines (user_id, started_at)
          WHERE ended_at IS NULL
        """
    )
    _rls("routines")

    # -----------------------------------------------------------
    # Ocorrência materializada
    # -----------------------------------------------------------
    op.create_table(
        "routine_occurrences",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("routine_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "scheduled_start", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "scheduled_end", sa.DateTime(timezone=True), nullable=False
        ),
        # Referência discriminada ao evento, como em `task_event_links`:
        # pode estar em `local_events` ou em `events_shadow`.
        sa.Column("event_ref_kind", sa.Text(), nullable=True),
        sa.Column("event_ref_id", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default="planned"
        ),
        sa.Column("actual_min", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True),
        # Para onde foi, quando `status = 'moved'`. Sem isso, "movido"
        # seria indistinguível de "pulado" no relatório.
        sa.Column("moved_to", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["core.users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["routine_id"], ["core.routines.id"], ondelete="CASCADE"
        ),
        # A chave natural da ocorrência. Materializar duas vezes o mesmo
        # horário duplicaria a aderência, e o `UNIQUE` é o que torna a
        # materialização idempotente — ela roda de novo a cada janela.
        sa.UniqueConstraint(
            "routine_id",
            "scheduled_start",
            name="routine_occurrences_unique",
        ),
        sa.CheckConstraint(
            "status IN ('planned', 'done', 'skipped', 'moved')",
            name="routine_occurrences_status_valid",
        ),
        sa.CheckConstraint(
            "actual_min IS NULL OR actual_min > 0",
            name="routine_occurrences_actual_valid",
        ),
        # Registrar um fato exige saber quando foi registrado — é o que
        # permite distinguir "pulei ontem" de "pulei na semana passada
        # e só agora contei".
        sa.CheckConstraint(
            "status = 'planned' OR recorded_at IS NOT NULL",
            name="routine_occurrences_recorded_coherent",
        ),
        schema="core",
    )
    op.create_index(
        "routine_occurrences_window_idx",
        "routine_occurrences",
        ["user_id", "scheduled_start"],
        schema="core",
    )
    op.create_index(
        "routine_occurrences_routine_idx",
        "routine_occurrences",
        ["routine_id", "scheduled_start"],
        schema="core",
    )
    _rls("routine_occurrences")

    # -----------------------------------------------------------
    # Mudança externa (§5.10)
    # -----------------------------------------------------------
    op.create_table(
        "external_change_log",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("change_kind", sa.Text(), nullable=False),
        sa.Column("event_ref_kind", sa.Text(), nullable=True),
        sa.Column("event_ref_id", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        # O que do Age colidiu com essa mudança. É o que transforma
        # "apareceu uma reunião" em "apareceu uma reunião que bate com
        # a sessão de Álgebra Linear" — a diferença entre informação e
        # ruído.
        sa.Column("conflicts_with_kind", sa.Text(), nullable=True),
        sa.Column("conflicts_with_id", sa.Text(), nullable=True),
        sa.Column("conflicts_with_title", sa.Text(), nullable=True),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # O que torna §5.10 reativo e não repetitivo: a mudança é
        # anunciada UMA vez, na próxima abertura do chat. Sem esta
        # coluna, a mesma reunião voltaria em toda mensagem.
        sa.Column(
            "acknowledged_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["core.users.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "change_kind IN ('created', 'moved', 'deleted', 'updated')",
            name="external_change_log_kind_valid",
        ),
        schema="core",
    )
    op.execute(
        """
        CREATE INDEX external_change_log_pending_idx
          ON core.external_change_log (user_id, detected_at DESC)
          WHERE acknowledged_at IS NULL
        """
    )
    _rls("external_change_log")

    # -----------------------------------------------------------
    # Rotina na sombra e no calendário nativo
    # -----------------------------------------------------------
    # Permite responder "fui à academia hoje?" a partir do evento, e
    # saber que mover este evento é mover uma ocorrência de série.
    op.add_column(
        "local_events",
        sa.Column("routine_id", sa.UUID(as_uuid=True), nullable=True),
        schema="core",
    )
    op.create_foreign_key(
        "local_events_routine_id_fkey",
        "local_events",
        "routines",
        ["routine_id"],
        ["id"],
        source_schema="core",
        referent_schema="core",
        ondelete="SET NULL",
    )
    op.add_column(
        "events_shadow",
        sa.Column("routine_id", sa.UUID(as_uuid=True), nullable=True),
        schema="core",
    )

    # -----------------------------------------------------------
    # Sobre GRANTs: nenhum aqui, e é correto.
    # -----------------------------------------------------------
    # `init-db.sql` define ALTER DEFAULT PRIVILEGES FOR ROLE age_owner
    # IN SCHEMA core, então estas tabelas já nascem com `core_api`
    # escrevendo e `ai_worker` só lendo (§11.2).
    #
    # A exceção que vale notar: o worker É quem detecta mudança externa
    # (o sync roda nele, §3.4), mas NÃO escreve em
    # `external_change_log` — ele reporta pela rota interna do core-api,
    # como faz com `plan_drafts`. Manter a escrita num lugar só é o que
    # impede a regra de "o que conta como conflito" de existir em duas
    # versões.


def downgrade() -> None:
    op.drop_column("events_shadow", "routine_id", schema="core")
    op.drop_constraint(
        "local_events_routine_id_fkey",
        "local_events",
        schema="core",
        type_="foreignkey",
    )
    op.drop_column("local_events", "routine_id", schema="core")
    op.drop_table("external_change_log", schema="core")
    op.drop_table("routine_occurrences", schema="core")
    op.drop_table("routines", schema="core")
