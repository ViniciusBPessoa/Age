"""baseline do schema ai: registro de turnos

Revision ID: 0001_baseline_ai
Revises:
Create Date: 2026-10-07

O schema `ai` guarda o que o worker produz: turnos, mensagens, uso de
tokens. No M0 só o essencial para o job de eco registrar o que fez.

Nota sobre FK: não há FK física para `core.users`. Schemas de donos
diferentes com migrations independentes não podem depender da ordem de
aplicação um do outro — a integridade é garantida pela aplicação, que só
enfileira job depois de validar o usuário (ARQUITETURA.md §11.4).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_baseline_ai"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "turns",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("provider_key", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "completion_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued','running','done','failed','cancelled')",
            name="turns_status_valid",
        ),
        schema="ai",
    )

    op.create_index(
        "turns_user_created_idx",
        "turns",
        ["user_id", sa.text("created_at DESC")],
        schema="ai",
    )

    # Guarda de idempotência: a fila é at-least-once, então um job
    # reentregue não pode refazer o turno e cobrar os tokens de novo
    # (§5.1). O índice parcial permite achar turno em andamento rápido.
    op.create_index(
        "turns_running_idx",
        "turns",
        ["status"],
        schema="ai",
        postgresql_where=sa.text("status IN ('queued','running')"),
    )

    op.execute("ALTER TABLE ai.turns ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ai.turns FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY own_rows ON ai.turns
          USING (user_id = current_setting('app.user_id')::uuid)
          WITH CHECK (user_id = current_setting('app.user_id')::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS own_rows ON ai.turns")
    op.drop_index("turns_running_idx", table_name="turns", schema="ai")
    op.drop_index("turns_user_created_idx", table_name="turns", schema="ai")
    op.drop_table("turns", schema="ai")
