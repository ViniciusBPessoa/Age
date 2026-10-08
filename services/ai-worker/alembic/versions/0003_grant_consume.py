"""Permite ao core-api marcar as saídas como consumidas

Revision ID: 0003_grant_consume
Revises: 0002_turn_outputs
Create Date: 2026-10-08

As tabelas de entrega (`ai.turn_outputs`, `ai.title_suggestions`) são do
worker, que as escreve. Mas o **core-api** é quem copia o conteúdo para
`core` e precisa marcar a linha como consumida, senão a copiaria de novo
a cada leitura.

Este GRANT é estreito de propósito: `UPDATE` apenas nas colunas de
controle de entrega, não no conteúdo. O core-api não pode alterar o que
o modelo respondeu — só registrar que já pegou.

É o tipo de permissão que vale ser cirúrgica: "dar UPDATE na tabela"
seria mais simples de escrever e abriria a porta para o core-api
reescrever resposta de modelo, que não é papel dele.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_grant_consume"
down_revision: str | None = "0002_turn_outputs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("GRANT UPDATE (consumed) ON ai.turn_outputs TO core_api")
    op.execute("GRANT UPDATE (applied) ON ai.title_suggestions TO core_api")


def downgrade() -> None:
    op.execute("REVOKE UPDATE (consumed) ON ai.turn_outputs FROM core_api")
    op.execute("REVOKE UPDATE (applied) ON ai.title_suggestions FROM core_api")
