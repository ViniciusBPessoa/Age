"""Cota mensal por usuário, para o modo de chave de instância

Revision ID: 0010_instance_key_quota
Revises: 0009_m5_routines
Create Date: 2026-10-14

ARQUITETURA.md §14.2 previu este modo: "o operador fornece a chave com
cota por usuário — o esquema já suporta, mas aí o custo passa a ser
seu". O que faltava era onde guardar o limite.

**A cota é uma coluna da conta, não uma constante do código.** Essa
escolha é o que torna cobrança, no dia em que existir, uma mudança de
DADO em vez de uma mudança no caminho crítico do turno: confirmar um
pagamento passa a ser escrever outro número aqui, com o código do
worker intocado. Com o limite numa constante, ligar plano pago exigiria
mexer no caminho que todo turno percorre, com usuários em cima.

**NULL e 0 significam ilimitado**, e isso é deliberado: "sem limite" é
uma intenção, não um número alto. Representar com um teto gigante
obrigaria a adivinhar um valor que nunca seja atingido, e liberar uma
conta específica — a do operador, por exemplo — passaria a depender
desse chute. O default é NULL para que as contas que já existem não
mudem de comportamento ao migrar.

A coluna é `numeric(10,4)` e não float: é dinheiro. Float acumula erro
de arredondamento em soma, e a comparação "usado >= limite" decide se
alguém consegue ou não mandar mensagem.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_instance_key_quota"
down_revision: str | None = "0009_m5_routines"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("monthly_quota_usd", sa.Numeric(10, 4), nullable=True),
        schema="core",
    )

    # Nenhum GRANT aqui, e isso foi verificado em vez de suposto:
    # `information_schema.role_table_grants` mostra que `ai_worker` já
    # tem SELECT e `core_api` tem SELECT/INSERT/UPDATE/DELETE em
    # `core.users`, concedidos no nível da TABELA — e privilégio de
    # tabela cobre coluna nova automaticamente. Um GRANT redundante aqui
    # sugeriria uma necessidade que não existe.


def downgrade() -> None:
    op.drop_column("users", "monthly_quota_usd", schema="core")
