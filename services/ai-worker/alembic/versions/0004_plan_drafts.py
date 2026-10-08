"""Rascunhos de plano no schema do worker

Revision ID: 0004_plan_drafts
Revises: 0003_grant_consume
Create Date: 2026-10-08

Mesmo padrão de `turn_outputs` (migration 0002), pela mesma razão: o
worker produz a proposta, mas `core.plans` é do core-api e o worker só
tem SELECT lá (§2.3.2).

Então o worker grava o rascunho aqui, e o core-api o promove a
`core.plans` ao ler — com a validação de conflito acontecendo do lado
que é dono da agenda. Isso tem um efeito bom além da permissão: a
validação roda contra o estado da agenda no momento da LEITURA, não da
proposta, então um conflito que surgiu nesse intervalo aparece.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_plan_drafts"
down_revision: str | None = "0003_grant_consume"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plan_drafts",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("turn_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        # Lista de itens como o worker os montou. A tradução para
        # `core.plan_items`, com resolução de evento e validação de
        # conflito, é do core-api.
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column(
            "promoted", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("promoted_plan_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["turn_id"], ["ai.turns.id"], ondelete="CASCADE"),
        schema="ai",
    )
    op.create_index(
        "plan_drafts_pending_idx",
        "plan_drafts",
        ["user_id", "created_at"],
        schema="ai",
        postgresql_where=sa.text("promoted = false"),
    )

    op.execute("ALTER TABLE ai.plan_drafts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ai.plan_drafts FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY own_rows ON ai.plan_drafts
          USING (user_id = NULLIF(current_setting('app.user_id', true), '')::uuid)
          WITH CHECK (user_id = NULLIF(current_setting('app.user_id', true), '')::uuid)
        """
    )

    # O core-api marca como promovido. GRANT cirúrgico: só a coluna de
    # controle, não o conteúdo — ele não pode reescrever a proposta.
    op.execute(
        "GRANT UPDATE (promoted, promoted_plan_id) ON ai.plan_drafts TO core_api"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS own_rows ON ai.plan_drafts")
    op.drop_table("plan_drafts", schema="ai")
