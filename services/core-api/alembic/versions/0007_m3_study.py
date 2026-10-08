"""M3: assuntos, tópicos e sessões de estudo

Revision ID: 0007_m3_study
Revises: 0006_m2_calendar
Create Date: 2026-10-08

ARQUITETURA.md §6.1. Três tabelas que sustentam o acompanhamento (§5.9.8):

- `study_subjects` — o assunto e o prazo
- `study_topics` — a decomposição, com a estimativa do modelo
- `study_sessions` — cada bloco agendado, e o que de fato aconteceu

A coluna que mais importa é `study_sessions.actual_min`: é ela que
permite recalibrar as estimativas. Sem registrar quanto tempo a sessão
realmente levou, o assistente repetiria para sempre o mesmo palpite
errado.

`estimated_min` fica em `study_topics` e não é sobrescrito: o histórico
da estimativa original importa para o usuário entender se o Age está
melhorando.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_m3_study"
down_revision: str | None = "0006_m2_calendar"
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
    # Assunto
    # -----------------------------------------------------------
    op.create_table(
        "study_subjects",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("deadline", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("total_estimated_min", sa.Integer(), nullable=True),
        # Ementa que o usuário colou, se colou. Quando existe, a
        # decomposição usa ela em vez do conhecimento geral (§5.5) — e
        # a diferença de qualidade é grande.
        sa.Column("syllabus_text", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("plan_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active','completed','abandoned')",
            name="study_subjects_status_valid",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["core.users.id"], ondelete="CASCADE"),
        # O plano pode ser apagado sem levar o assunto: os eventos que
        # ele criou continuam na agenda (§6.2.3).
        sa.ForeignKeyConstraint(
            ["plan_id"], ["core.plans.id"], ondelete="SET NULL"
        ),
        schema="core",
    )
    op.create_index(
        "study_subjects_active_idx",
        "study_subjects",
        ["user_id", "deadline"],
        schema="core",
        postgresql_where=sa.text("status = 'active'"),
    )
    _rls("study_subjects")

    # -----------------------------------------------------------
    # Tópico
    # -----------------------------------------------------------
    op.create_table(
        "study_topics",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        # A estimativa ORIGINAL do modelo. Nunca sobrescrita: guardar o
        # palpite inicial é o que permite mostrar ao usuário se o Age
        # está calibrando bem (§5.9.8).
        sa.Column("estimated_min", sa.Integer(), nullable=False),
        # Estimativa corrigida pelo tempo real das sessões concluídas.
        sa.Column("calibrated_min", sa.Integer(), nullable=True),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prerequisites", sa.JSON(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        # Marcado quando o usuário diz "o tópico 3 eu já sei" (§5.9.7).
        sa.Column(
            "skipped_by_user",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','in_progress','completed','skipped')",
            name="study_topics_status_valid",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["core.users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["subject_id"], ["core.study_subjects.id"], ondelete="CASCADE"
        ),
        schema="core",
    )
    op.create_index(
        "study_topics_subject_idx",
        "study_topics",
        ["subject_id", "order_index"],
        schema="core",
    )
    _rls("study_topics")

    # -----------------------------------------------------------
    # Sessão
    # -----------------------------------------------------------
    op.create_table(
        "study_sessions",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("topic_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_item_id", sa.UUID(as_uuid=True), nullable=True),
        # Qual evento na agenda corresponde a esta sessão. Texto e não
        # FK porque pode apontar para `local_events` ou para o id
        # externo de um provedor (§6.3).
        sa.Column("event_ref", sa.Text(), nullable=True),
        sa.Column("scheduled_start", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("scheduled_end", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False, server_default="study"),
        sa.Column("part", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("of_parts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.Text(), nullable=False, server_default="planned"),
        # O tempo REAL. É esta coluna que alimenta a recalibração.
        sa.Column("actual_min", sa.Integer(), nullable=True),
        # 1..5, opcional. Sinaliza tópico mais difícil que o estimado.
        sa.Column("difficulty_rating", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("recorded_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "kind IN ('study','review')", name="study_sessions_kind_valid"
        ),
        sa.CheckConstraint(
            "status IN ('planned','done','skipped','rescheduled')",
            name="study_sessions_status_valid",
        ),
        sa.CheckConstraint(
            "difficulty_rating IS NULL OR difficulty_rating BETWEEN 1 AND 5",
            name="study_sessions_difficulty_valid",
        ),
        sa.CheckConstraint(
            "scheduled_end >= scheduled_start",
            name="study_sessions_time_valid",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["core.users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["topic_id"], ["core.study_topics.id"], ondelete="CASCADE"
        ),
        schema="core",
    )
    op.create_index(
        "study_sessions_topic_idx",
        "study_sessions",
        ["topic_id", "scheduled_start"],
        schema="core",
    )
    # Marcação rápida na timeline (§8.1): acha o que já passou e ainda
    # não foi marcado.
    op.create_index(
        "study_sessions_pending_idx",
        "study_sessions",
        ["user_id", "scheduled_start"],
        schema="core",
        postgresql_where=sa.text("status = 'planned'"),
    )
    _rls("study_sessions")

    # -----------------------------------------------------------
    # Preferências aprendidas (§5.9.7)
    # -----------------------------------------------------------
    # "Nada antes das 10h" dito numa conversa deve valer para sempre, se
    # o usuário confirmar. Sem isto ele corrige a mesma coisa toda
    # semana — a reclamação mais previsível do produto.
    op.create_table(
        "learned_preferences",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        # Onde veio: conversa, correção de plano, ou ajuste manual.
        sa.Column("source", sa.Text(), nullable=False, server_default="chat"),
        sa.Column("conversation_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "confirmed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("user_id", "key", name="learned_pref_unique"),
        sa.ForeignKeyConstraint(["user_id"], ["core.users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["core.conversations.id"],
            ondelete="SET NULL",
        ),
        schema="core",
    )
    _rls("learned_preferences")


def downgrade() -> None:
    for table in (
        "learned_preferences",
        "study_sessions",
        "study_topics",
        "study_subjects",
    ):
        op.execute(f"DROP POLICY IF EXISTS own_rows ON core.{table}")
        op.drop_table(table, schema="core")
