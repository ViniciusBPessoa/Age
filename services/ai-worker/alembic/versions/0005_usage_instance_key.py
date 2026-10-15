"""Marca, em cada turno, se o custo foi da chave da instância

Revision ID: 0005_usage_instance_key
Revises: 0004_plan_drafts
Create Date: 2026-10-14

`ai.turn_usage` já guardava tokens e `estimated_cost_usd` por turno
desde o M1 — a medição que a cota de §3451 precisa existia antes de a
cota existir. O que faltava era distinguir **de quem foi o custo**.

Sem esta coluna, a soma do mês incluiria turnos pagos pela chave do
próprio usuário, e quem colasse a chave dele veria a cota do operador
cair por gasto que ele mesmo bancou. A consulta da cota filtra por
`used_instance_key = true` justamente por isso.

**Default `false`**, que é o valor correto para o histórico: todos os
turnos anteriores a esta migration usaram chave de usuário, porque o
modo de instância não existia. Não é um default escolhido por
conveniência — é o fato.

A coluna é `NOT NULL`: "não sei de quem foi o custo" não é um estado que
valha a pena representar numa tabela que decide se alguém pode mandar
mensagem.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_usage_instance_key"
down_revision: str | None = "0004_plan_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "turn_usage",
        sa.Column(
            "used_instance_key",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema="ai",
    )

    # A cota soma `estimated_cost_usd` do mês corrente filtrando por
    # `used_instance_key`, e isso roda ANTES de cada turno com chave de
    # instância — é consulta de caminho crítico, não de relatório.
    #
    # O índice existente é `(user_id, created_at DESC)`, que já serve
    # para a janela do mês; este o estende com a coluna do filtro, e é
    # parcial porque só as linhas `true` são somadas. Parcial mantém o
    # índice do tamanho do que a instância pagou, não do tráfego todo.
    op.create_index(
        "turn_usage_instance_month_idx",
        "turn_usage",
        ["user_id", "created_at"],
        schema="ai",
        postgresql_where=sa.text("used_instance_key"),
    )


def downgrade() -> None:
    op.drop_index(
        "turn_usage_instance_month_idx", table_name="turn_usage", schema="ai"
    )
    op.drop_column("turn_usage", "used_instance_key", schema="ai")
