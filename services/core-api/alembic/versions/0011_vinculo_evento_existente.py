"""Vínculo de tarefa com bloco que JÁ EXISTE na agenda

Revision ID: 0011_vinculo_evento_existente
Revises: 0010_instance_key_quota
Create Date: 2026-10-19

§5.7.1 promete que tarefa e bloco andam juntos, e o Age afirmava ter
ligado os dois sem ligar nada. O defeito foi medido na conta de
verdade: o usuário pediu, com essas palavras, "criar alguns tudulos
vinculados a esses horários"; a resposta foi "montei 8 itens, todos
ligados ao bloco em que cada um cabe"; e as 8 linhas de `plan_items`
saíram com `links = []`, sem uma única linha em `task_event_links`.

A causa tinha duas metades, e a segunda é esta coluna. `plan_items.links`
guarda ids de itens DO MESMO PLANO, e é o que `Plan.linked_group` percorre
para formar o grupo de APROVAÇÃO: aprovar um item aprova o grupo, e a
falha de um reverte o resto. Um bloco que já está na agenda não cabe
nessa estrutura por dois motivos independentes:

1. **Semântico.** Não há o que aprovar nem o que reverter num evento que
   já foi aplicado. Pôr o id em `links` arrastaria um evento aplicado
   para dentro do acordo, e a UI ofereceria desfazer um bloco que não
   faz parte da proposta.
2. **Mecânico.** `_persist_task_event_links` resolve cada id com
   `plan.item(...)`, que devolve `None` para quem não é item do plano, e
   o vínculo era descartado em silêncio. Era por isso que consertar só o
   lado do modelo não produziria um único vínculo.

Daí uma coluna separada em vez de um significado novo para `links` — a
mesma decisão que `reorder_ids` recebeu em 0008, e pelo mesmo argumento:
uma coluna cujo conteúdo muda de sentido conforme a operação é uma
coluna que obriga todo leitor a adivinhar.

**Por que json e não uma tabela de ligação.** O par só precisa viver
entre propor e aplicar; o vínculo definitivo tem tabela própria há duas
migrações (`core.task_event_links`, 0008). Uma tabela aqui duplicaria
esse papel com tempo de vida diferente.

**Por que o par `(kind, id)` e não o id sozinho.** `local_events` e
`events_shadow` têm as duas a PK `uuid`, então o id não diz em qual
tabela procurar, e `task_event_links` tem CHECK em `event_ref_kind`.
Carregar o `kind` desde a proposta é o que permite gravar o vínculo sem
adivinhar a origem do bloco.

Nullable e sem default: plano antigo não tem vínculo a bloco existente,
e `NULL` lido como tupla vazia já é o comportamento certo. Nenhum GRANT
é necessário — privilégio concedido no nível da tabela cobre coluna
nova, e `core.plan_items` já tem os de `core_api`.

**A operação `LINK` também entra no CHECK de `op`.** Não é detalhe: a
primeira versão desta migração só criou a coluna, e o item de `LINK`
morria em `plan_items_op_valid` no INSERT. O rascunho era descartado
com um WARNING no log e o usuário via um plano vazio — falha silenciosa
do mesmo tipo que esta mudança existe para consertar. Só apareceu
porque o teste de integração roda contra Postgres de verdade; suíte
com banco falso teria passado.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_vinculo_evento_existente"
down_revision: str | None = "0010_instance_key_quota"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_OPS_ANTES = "'CREATE', 'MOVE', 'UPDATE', 'DELETE', 'COMPLETE', 'REORDER'"
_OPS_DEPOIS = _OPS_ANTES + ", 'LINK'"


def upgrade() -> None:
    op.add_column(
        "plan_items",
        sa.Column("event_refs", sa.JSON(), nullable=True),
        schema="core",
    )

    op.drop_constraint(
        "plan_items_op_valid", "plan_items", schema="core", type_="check"
    )
    op.create_check_constraint(
        "plan_items_op_valid",
        "plan_items",
        f"op IN ({_OPS_DEPOIS})",
        schema="core",
    )


def downgrade() -> None:
    # Itens de `LINK` precisam sair antes de o CHECK voltar a recusá-los:
    # com eles no lugar, `create_check_constraint` falha na validação da
    # tabela e o downgrade fica impossível.
    op.execute("DELETE FROM core.plan_items WHERE op = 'LINK'")
    op.drop_constraint(
        "plan_items_op_valid", "plan_items", schema="core", type_="check"
    )
    op.create_check_constraint(
        "plan_items_op_valid",
        "plan_items",
        f"op IN ({_OPS_ANTES})",
        schema="core",
    )
    op.drop_column("plan_items", "event_refs", schema="core")
