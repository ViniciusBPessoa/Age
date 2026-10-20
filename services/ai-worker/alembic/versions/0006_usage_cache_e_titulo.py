"""Tokens de cache no uso, e uso sem turno

Revision ID: 0006_usage_cache_e_titulo
Revises: 0005_usage_instance_key
Create Date: 2026-10-20

Duas mudanças que o relatório de custo precisa, e as duas existem porque
o custo gravado hoje é maior que o real.

## `cached_prompt_tokens`

O DeepSeek devolve `prompt_cache_hit_tokens` e
`prompt_cache_miss_tokens`, e o hit custa 1/50 do miss (0,003 contra
0,15 por milhão). O adaptador lia só `prompt_tokens` e descartava o
resto, então todo prompt era cobrado como se nada viesse de cache. Com
um prompt grande e quase igual entre turnos — a forma do prompt do Age —
isso superestimava a maior parte da conta.

A coluna guarda a evidência: `estimated_cost_usd` passa a ser auditável
contra os tokens que o geraram, em vez de ser um número de origem
esquecida.

**Nullable, e `NULL` não é zero.** Provedor que não reporta cache não
tem esse dado, e `0` afirmaria "nada veio de cache" — que é uma medição,
não uma ausência. A diferença tem uso prático: `0` sustentado num prompt
grande é sinal de que o cache do provedor parou de pegar, e um default
zero apagaria esse sinal.

## `turn_id` passa a aceitar `NULL`

`maintenance.py` gasta uma chamada de LLM por conversa para gerar o
título, e o comentário dela afirmava:

    "O gasto segue registrado em `turn_usage`, então aparece no
    relatório de custo mesmo não descontando da cota."

Não aparecia. O único INSERT daquele arquivo é em
`ai.title_suggestions`; o `completion.usage` era descartado. O custo era
invisível, e o comentário prometia o contrário — o pior tipo de
documentação, porque impede a pergunta.

Gravar esse uso exigia um `turn_id`, e o título não tem turno: ele não
nasce de `POST /messages`, nasce de um job agendado depois da primeira
mensagem. As opções eram inventar uma linha em `ai.turns` para cada
título — um turno que não existiu, poluindo a tabela que o SSE e a
idempotência usam — ou admitir que existe uso sem turno. A segunda é a
verdade, então é ela que vai para o schema.

A FK para `ai.turns` continua: `NULL` não é verificado por FK, então a
integridade de quem TEM turno fica igual. O que se perde é o `NOT NULL`
como garantia de "todo uso veio de um turno", e essa garantia era falsa
desde que o título passou a gastar.

Nenhum GRANT é necessário: privilégio de tabela cobre coluna nova, e
`core_api` já tem SELECT em `ai` pelo schema de leitura.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_usage_cache_e_titulo"
down_revision: str | None = "0005_usage_instance_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "turn_usage",
        sa.Column("cached_prompt_tokens", sa.Integer(), nullable=True),
        schema="ai",
    )
    op.alter_column(
        "turn_usage",
        "turn_id",
        existing_type=sa.UUID(as_uuid=True),
        nullable=True,
        schema="ai",
    )


def downgrade() -> None:
    # As linhas sem turno precisam sair antes de o `NOT NULL` voltar,
    # senão o ALTER falha na validação e o downgrade fica impossível.
    # São linhas de custo de título: perder a trilha é o preço de
    # reverter, e é menor que um banco que não desce.
    op.execute("DELETE FROM ai.turn_usage WHERE turn_id IS NULL")
    op.alter_column(
        "turn_usage",
        "turn_id",
        existing_type=sa.UUID(as_uuid=True),
        nullable=False,
        schema="ai",
    )
    op.drop_column("turn_usage", "cached_prompt_tokens", schema="ai")
