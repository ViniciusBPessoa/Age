"""Custo por mensagem, dedupe honesto do uso, e o flag de exibição

Revision ID: 0012_custo_por_mensagem
Revises: 0011_vinculo_evento_existente
Create Date: 2026-10-20

Três colunas, e a do meio conserta um defeito que já aconteceu neste
banco.

## `core.messages.estimated_cost_usd`

A mensagem já guarda `prompt_tokens` e `completion_tokens` desde o M1 —
o dreno os copia de `ai.turn_usage` num LEFT JOIN. Faltava o custo, que
é o que a tela precisa para dizer quanto aquela resposta custou.

Nullable, e `NULL` não é zero: mensagem anterior a esta migration,
mensagem do próprio usuário (o custo é do par e fica na resposta, que é
onde a cobrança acontece) e modelo sem preço conhecido todos querem
"não sei". `0.0` afirmaria que foi de graça.

`numeric(12,6)` e não float, igual a `usage_log`: é dinheiro. A
precisão importa de verdade aqui — uma resposta com prompt cacheado
custa na casa de 0,00002, e `numeric(12,4)` arredondaria para zero.

## `core.usage_log.turn_id` e a unique parcial

A idempotência do dreno era um `NOT EXISTS` sobre
`(user_id, created_at, model)` — sem constraint nenhuma no banco. Isso
falha por construção, e não só na teoria: num `INSERT ... SELECT` o
`NOT EXISTS` é avaliado contra o snapshot do início da instrução, então
ele **não vê as linhas que a própria instrução está inserindo**. Dois
turnos do mesmo usuário com o mesmo `now()` e o mesmo modelo passam os
dois pela checagem e entram os dois. Dali em diante a duplicata é
permanente, porque o dreno seguinte acha uma delas e dá a chave por
satisfeita.

Não é hipótese. Medido neste banco antes da migration:

    total | chaves_distintas
      245 |              244

Uma linha duplicada, dois turnos de 20/12 tokens no mesmo instante. O
efeito num relatório de custo é tokens e custo dobrados, sem erro em
lugar nenhum — exatamente a classe de falha silenciosa que o cabeçalho
de `turn_sync.py` já descreve para `turn_outputs`, e que lá foi
resolvida de forma estrutural (`FOR UPDATE SKIP LOCKED`). Aqui a
solução estrutural é a chave natural: `turn_id`, com unique, e
`ON CONFLICT DO NOTHING` no lugar da heurística.

A unique é **parcial** (`WHERE turn_id IS NOT NULL`) por duas razões. A
primeira é o histórico: as linhas de antes desta migration não têm
turno, e um unique total as rejeitaria em bloco. A segunda é que uso
sem turno passou a ser legítimo — a geração de título gasta e não tem
turno (ver `0006_usage_cache_e_titulo` no schema `ai`).

O DELETE de desduplicação roda ANTES de criar o índice, senão
`CREATE UNIQUE INDEX` falha na validação e a migration fica
inaplicável. A chave do DELETE inclui os tokens, não só
`(user_id, created_at, model)`: dois turnos genuinamente distintos no
mesmo microssegundo quase certamente diferem em contagem de token, e
incluí-los reduz o falso positivo ao caso praticamente impossível. O
trade-off é assumido: a alternativa é um banco que não migra.

## `core.user_preferences.show_cost_usd`

O custo em dinheiro tem de ser fácil de esconder depois, e "fácil" aqui
significa um UPDATE — não um deploy. A coluna é a camada por conta; o
teto da instância é `cost_display_enabled` em `config.py`, e a
preferência só pode DESLIGAR o que o operador deixou ligado.

`NOT NULL DEFAULT true` porque hoje o custo é visível, e "não sei se
mostro dinheiro" não é um estado útil.

Nenhum GRANT é necessário: privilégio de tabela cobre coluna nova, e
`core_api` já tem os das três tabelas.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_custo_por_mensagem"
down_revision: str | None = "0011_vinculo_evento_existente"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("estimated_cost_usd", sa.Numeric(12, 6), nullable=True),
        schema="core",
    )

    op.add_column(
        "usage_log",
        sa.Column("turn_id", sa.UUID(as_uuid=True), nullable=True),
        schema="core",
    )
    # Desduplica o que o `NOT EXISTS` deixou passar. Sem isto o índice
    # abaixo não é criável num banco que já rodou turnos concorrentes.
    #
    # `NO FORCE` em volta, e isto não é zelo: a migration roda como
    # `age_owner`, que NÃO tem `BYPASSRLS` (decisão de §4.2 — o projeto
    # não concede bypass a ninguém), e `FORCE ROW LEVEL SECURITY` vale
    # inclusive para o dono da tabela. Sem `app.user_id` definido a
    # policy `own_rows` não casa linha nenhuma, então o DELETE apagaria
    # ZERO e não diria nada — e o `CREATE UNIQUE INDEX` logo abaixo
    # falharia depois, com um erro que não aponta para a causa.
    #
    # Foi o que aconteceu na primeira execução desta migration: ela
    # "passou", as 245 linhas continuaram 245 com 244 chaves, e a
    # duplicata seguiu no banco. Varredura de manutenção em tabela com
    # RLS forçada precisa suspender a força explicitamente.
    op.execute("ALTER TABLE core.usage_log NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE core.usage_log DISABLE ROW LEVEL SECURITY")
    try:
        op.execute(
            """
            DELETE FROM core.usage_log a
            USING core.usage_log b
            WHERE a.user_id = b.user_id
              AND a.created_at = b.created_at
              AND a.model = b.model
              AND a.prompt_tokens = b.prompt_tokens
              AND a.completion_tokens = b.completion_tokens
              AND a.id > b.id
            """
        )
    finally:
        # Religar é obrigatório mesmo se o DELETE falhar: uma tabela que
        # sai desta migration sem RLS é vazamento entre contas, que é
        # pior que uma migration que não completa.
        op.execute("ALTER TABLE core.usage_log ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE core.usage_log FORCE ROW LEVEL SECURITY")
    op.create_index(
        "usage_log_turn_unique",
        "usage_log",
        ["turn_id"],
        unique=True,
        schema="core",
        postgresql_where=sa.text("turn_id IS NOT NULL"),
    )

    op.add_column(
        "user_preferences",
        sa.Column(
            "show_cost_usd",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        schema="core",
    )


def downgrade() -> None:
    op.drop_column("user_preferences", "show_cost_usd", schema="core")
    op.drop_index("usage_log_turn_unique", "usage_log", schema="core")
    op.drop_column("usage_log", "turn_id", schema="core")
    op.drop_column("messages", "estimated_cost_usd", schema="core")
