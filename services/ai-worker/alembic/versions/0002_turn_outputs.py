"""M1: saídas do turno no schema do worker

Revision ID: 0002_turn_outputs
Revises: 0001_baseline_ai
Create Date: 2026-10-08

Corrige um furo de projeto que a fronteira de privilégio do banco
expôs na primeira execução real: eu havia posto o worker escrevendo em
`core.messages`, `core.conversations` e `core.usage_log`. O Postgres
recusou — `permission denied for table conversations` — porque o
`ai_worker` só tem SELECT em `core` (§2.3.2).

O GRANT estava certo; o código estava errado. A tentação seria afrouxar
a permissão, e isso é exatamente como a fronteira apodrece.

A correção respeita o desenho: o worker escreve **no schema dele**, e o
`core-api` lê de lá. Três tabelas novas:

- `ai.turn_outputs`   — o texto que o modelo produziu
- `ai.turn_usage`     — tokens e custo estimado
- `ai.title_suggestions` — título gerado, que o core-api aplica

O `core-api` tem SELECT em `ai`, então ele compõe a conversa juntando
`core.messages` (o que o usuário escreveu) com `ai.turn_outputs` (o que
o modelo respondeu). Quem escreve cada coisa é quem é dono dela.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_turn_outputs"
down_revision: str | None = "0001_baseline_ai"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE ai.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE ai.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY own_rows ON ai.{table}
          USING (user_id = NULLIF(current_setting('app.user_id', true), '')::uuid)
          WITH CHECK (user_id = NULLIF(current_setting('app.user_id', true), '')::uuid)
        """
    )


def upgrade() -> None:
    # -----------------------------------------------------------
    # Resposta do modelo
    # -----------------------------------------------------------
    op.create_table(
        "turn_outputs",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("turn_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.Text(), nullable=False, server_default="assistant"),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_calls", sa.JSON(), nullable=True),
        sa.Column("tool_call_id", sa.Text(), nullable=True),
        # Ordem dentro do turno: um turno com ferramentas gera várias
        # linhas (assistant com tool_calls, tool com resultado, ...).
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider_key", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        # Marcado true quando o core-api já copiou para core.messages.
        sa.Column(
            "consumed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["turn_id"], ["ai.turns.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "role IN ('assistant','tool')", name="turn_outputs_role_valid"
        ),
        schema="ai",
    )
    op.create_index(
        "turn_outputs_turn_idx",
        "turn_outputs",
        ["turn_id", "sequence"],
        schema="ai",
    )
    # O core-api busca por aqui o que ainda não copiou.
    op.create_index(
        "turn_outputs_pending_idx",
        "turn_outputs",
        ["conversation_id", "created_at"],
        schema="ai",
        postgresql_where=sa.text("consumed = false"),
    )
    _rls("turn_outputs")

    # -----------------------------------------------------------
    # Uso e custo
    # -----------------------------------------------------------
    op.create_table(
        "turn_usage",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("turn_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_key", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(12, 6), nullable=True),
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
        "turn_usage_user_date_idx",
        "turn_usage",
        ["user_id", sa.text("created_at DESC")],
        schema="ai",
    )
    _rls("turn_usage")

    # -----------------------------------------------------------
    # Título sugerido (§6.2.2)
    # -----------------------------------------------------------
    op.create_table(
        "title_suggestions",
        sa.Column("conversation_id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False, server_default="llm"),
        sa.Column(
            "applied", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "source IN ('llm','fallback')", name="title_source_valid"
        ),
        schema="ai",
    )
    _rls("title_suggestions")


def downgrade() -> None:
    for table in ("title_suggestions", "turn_usage", "turn_outputs"):
        op.execute(f"DROP POLICY IF EXISTS own_rows ON ai.{table}")
        op.drop_table(table, schema="ai")
