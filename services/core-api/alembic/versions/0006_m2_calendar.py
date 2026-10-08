"""M2: calendário nativo, conexões externas e planos

Revision ID: 0006_m2_calendar
Revises: 0005_message_idempotency
Create Date: 2026-10-08

ARQUITETURA.md §6.1 e §6.3. Duas coisas que merecem atenção:

**1. Nenhuma coluna específica de provedor.** O estado de sync é um
cursor opaco (§3.2.2). Na v0.1 do documento havia `sync_token` e
`webhook_channel_id` — nomes do Google — direto no schema. Campo de
tabela é o lugar mais caro para errar: mudar depois exige migração de
dados.

**2. `local_events` e `events_shadow` não são a mesma coisa.**
`local_events` É a fonte de verdade do calendário nativo.
`events_shadow` é reflexo de provedor externo e NUNCA é autoridade —
divergência sempre se resolve adotando o estado do provedor, exceto nos
metadados que só existem aqui (flexibilidade, vínculo com plano).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_m2_calendar"
down_revision: str | None = "0005_message_idempotency"
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
    # Calendário nativo (§3.2.1) — default de conta nova
    # -----------------------------------------------------------
    op.create_table(
        "local_calendars",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "timezone", sa.Text(), nullable=False, server_default="America/Sao_Paulo"
        ),
        sa.Column("color", sa.Text(), nullable=True),
        sa.Column(
            "is_default", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["core.users.id"], ondelete="CASCADE"),
        schema="core",
    )
    _rls("local_calendars")

    op.create_table(
        "local_events",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("local_calendar_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("start_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("end_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "all_day", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("recurrence", sa.Text(), nullable=True),  # RRULE
        sa.Column("recurrence_parent_id", sa.UUID(as_uuid=True), nullable=True),
        # §5.6: viés para FIXED. Mover a consulta médica de alguém por
        # heurística errada destrói a confiança de uma vez.
        sa.Column(
            "flexibility", sa.Text(), nullable=False, server_default="fixed"
        ),
        sa.Column("flexibility_source", sa.Text(), nullable=True),
        sa.Column(
            "created_by_age", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("plan_item_id", sa.UUID(as_uuid=True), nullable=True),
        # Controle de concorrência otimista no calendário nativo.
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("end_at >= start_at", name="local_events_time_valid"),
        sa.CheckConstraint(
            "flexibility IN ('fixed','semi_flexible','flexible')",
            name="local_events_flex_valid",
        ),
        sa.ForeignKeyConstraint(
            ["local_calendar_id"],
            ["core.local_calendars.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["core.users.id"], ondelete="CASCADE"),
        schema="core",
    )
    # Consulta por janela é a operação mais frequente do produto.
    op.create_index(
        "local_events_window_idx",
        "local_events",
        ["user_id", "start_at", "end_at"],
        schema="core",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    _rls("local_events")

    # -----------------------------------------------------------
    # Conexões externas — sem coluna de provedor (§3.2.2)
    # -----------------------------------------------------------
    op.create_table(
        "calendar_connections",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_key", sa.Text(), nullable=False),
        # Blob cifrado; a forma interna varia por provedor e só o
        # adaptador a interpreta.
        sa.Column("encrypted_credentials", sa.LargeBinary(), nullable=False),
        sa.Column(
            "credential_expires_at", sa.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.Column("account_label", sa.Text(), nullable=True),
        sa.Column("capabilities", sa.JSON(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("last_synced_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('active','expired','revoked','error')",
            name="conn_status_valid",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["core.users.id"], ondelete="CASCADE"),
        schema="core",
    )
    _rls("calendar_connections")

    op.create_table(
        "calendar_selections",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("external_calendar_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("timezone", sa.Text(), nullable=True),
        sa.Column(
            "read_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "write_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "is_write_target", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        # §9.3 modo privado: contar como ocupado sem enviar o título.
        sa.Column(
            "send_titles_to_llm",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("color", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "connection_id", "external_calendar_id", name="selection_unique"
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["core.calendar_connections.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["core.users.id"], ondelete="CASCADE"),
        schema="core",
    )
    _rls("calendar_selections")

    # Estado de sync separado: cursor OPACO, um por calendário.
    op.create_table(
        "calendar_sync_state",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("selection_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("cursor_opaque", sa.Text(), nullable=True),
        sa.Column("cursor_issued_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("subscription_opaque", sa.Text(), nullable=True),
        sa.Column(
            "subscription_expires_at", sa.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.Column("last_full_resync_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint("selection_id", name="sync_state_unique"),
        sa.ForeignKeyConstraint(
            ["selection_id"],
            ["core.calendar_selections.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["core.users.id"], ondelete="CASCADE"),
        schema="core",
    )
    _rls("calendar_sync_state")

    # -----------------------------------------------------------
    # Sombra de eventos externos (§6.3)
    # -----------------------------------------------------------
    op.create_table(
        "events_shadow",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("external_calendar_id", sa.Text(), nullable=False),
        sa.Column("external_event_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("start_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("end_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "all_day", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("recurrence", sa.Text(), nullable=True),
        sa.Column("recurring_event_id", sa.Text(), nullable=True),
        sa.Column(
            "has_attendees", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        # Metadados que o provedor externo não tem onde guardar. É a
        # razão de a sombra existir, além de cache (§6.3).
        sa.Column(
            "flexibility", sa.Text(), nullable=False, server_default="fixed"
        ),
        sa.Column("flexibility_source", sa.Text(), nullable=True),
        sa.Column(
            "created_by_age", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("plan_item_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column(
            "last_seen_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "connection_id", "external_event_id", name="shadow_event_unique"
        ),
        sa.CheckConstraint(
            "flexibility IN ('fixed','semi_flexible','flexible')",
            name="shadow_flex_valid",
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["core.calendar_connections.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["core.users.id"], ondelete="CASCADE"),
        schema="core",
    )
    op.create_index(
        "shadow_window_idx",
        "events_shadow",
        ["user_id", "start_at", "end_at"],
        schema="core",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    _rls("events_shadow")

    # -----------------------------------------------------------
    # Planos (§5.7)
    # -----------------------------------------------------------
    op.create_table(
        "plans",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        # NULL quando a conversa foi apagada: o plano sobrevive, porque
        # os eventos que ele criou continuam na agenda (§6.2.3).
        sa.Column("conversation_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("origin_note", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="proposed"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # Plano expira: agenda muda, e aprovar proposta de três dias
        # atrás causaria estrago (§5.7).
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("applied_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('proposed','partially_applied','applied',"
            "'rejected','expired','failed')",
            name="plans_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["core.conversations.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["core.users.id"], ondelete="CASCADE"),
        schema="core",
    )
    op.create_index(
        "plans_user_status_idx",
        "plans",
        ["user_id", "status", sa.text("created_at DESC")],
        schema="core",
    )
    _rls("plans")

    op.create_table(
        "plan_items",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", sa.UUID(as_uuid=True), nullable=False),
        # Já preparado para o M4: tarefa e evento no mesmo diff (§5.7).
        sa.Column("target", sa.Text(), nullable=False, server_default="event"),
        sa.Column("op", sa.Text(), nullable=False),
        sa.Column("target_ref", sa.Text(), nullable=True),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        # Obrigatório: mover sem explicar não é permitido pelo schema
        # (§5.6).
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("conflicts", sa.JSON(), nullable=True),
        sa.Column("links", sa.JSON(), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("applied_ref", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("applied_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "op IN ('CREATE','MOVE','UPDATE','DELETE','COMPLETE','REORDER')",
            name="plan_items_op_valid",
        ),
        sa.CheckConstraint(
            "target IN ('event','task')", name="plan_items_target_valid"
        ),
        sa.CheckConstraint(
            "status IN ('pending','accepted','rejected','applied','failed')",
            name="plan_items_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"], ["core.plans.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["core.users.id"], ondelete="CASCADE"),
        schema="core",
    )
    op.create_index(
        "plan_items_plan_idx", "plan_items", ["plan_id", "sequence"], schema="core"
    )
    _rls("plan_items")

    # Snapshot para o undo de 7 dias (§5.8).
    op.create_table(
        "plan_undo_log",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("op", sa.Text(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.Column("applied_ref", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("undone_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["plan_id"], ["core.plans.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["core.users.id"], ondelete="CASCADE"),
        schema="core",
    )
    _rls("plan_undo_log")


def downgrade() -> None:
    for table in (
        "plan_undo_log",
        "plan_items",
        "plans",
        "events_shadow",
        "calendar_sync_state",
        "calendar_selections",
        "calendar_connections",
        "local_events",
        "local_calendars",
    ):
        op.execute(f"DROP POLICY IF EXISTS own_rows ON core.{table}")
        op.drop_table(table, schema="core")
