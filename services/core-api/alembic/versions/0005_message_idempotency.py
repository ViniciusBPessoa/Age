"""Idempotência de envio ancorada em core.messages

Revision ID: 0005_message_idempotency
Revises: 0004_rls_bootstrap_fix
Create Date: 2026-10-08

Corrige uma corrida real, pega pelo smoke do M1: um duplo-clique no
botão de enviar gravava a mensagem duas vezes.

A guarda original consultava `ai.turns`, mas aquela linha só passa a
existir quando o **worker** consome o job. Entre o primeiro e o segundo
POST — milissegundos — ela ainda não existe, então os dois passavam.

A correção ancora a idempotência onde o core-api realmente escreve:
`core.messages.client_turn_id`, com índice único. O segundo insert
colide no banco, que é o único árbitro confiável para corrida. O
`_job_id` do ARQ continua valendo como segunda camada, para o caso de o
job ser enfileirado duas vezes.

Lição que vale registrar: idempotência tem de ser garantida pelo
recurso que a operação cria, não por um recurso que outro serviço
criará depois.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_message_idempotency"
down_revision: str | None = "0004_rls_bootstrap_fix"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("client_turn_id", sa.UUID(as_uuid=True), nullable=True),
        schema="core",
    )
    # Parcial: só mensagens de usuário carregam o id do cliente, e
    # NULLs não conflitam entre si.
    op.create_index(
        "messages_client_turn_unique",
        "messages",
        ["client_turn_id"],
        unique=True,
        schema="core",
        postgresql_where=sa.text("client_turn_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "messages_client_turn_unique", table_name="messages", schema="core"
    )
    op.drop_column("messages", "client_turn_id", schema="core")
