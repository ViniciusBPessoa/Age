"""M4: listas, tarefas e o vínculo tarefa↔evento

Revision ID: 0008_m4_tasks
Revises: 0007_m3_study
Create Date: 2026-10-08

ARQUITETURA.md §6.1 e §3.3. Seis tabelas, em três grupos:

**Lista nativa** (`local_task_lists`, `local_tasks`) — o adaptador
`LocalTasks`, com capacidades plenas, default de conta nova pelo mesmo
motivo do calendário nativo (§3.3.3).

**Conexão externa** (`task_connections`, `task_list_selections`,
`tasks_shadow`) — mesmo padrão do calendário: **nenhuma coluna
específica de provedor**. Credencial e cursor são blobs opacos que só o
adaptador interpreta. Na v0.1 do documento havia `sync_token`, nome do
Google, numa coluna — o lugar mais caro de errar, porque coluna de
tabela é difícil de mudar depois.

**Vínculo** (`task_event_links`) — o par tarefa/evento de §5.7.1.

Duas decisões que merecem explicação:

1. **`tasks_shadow` tem colunas `local_*` separadas.** Não é
   duplicação: `due_date` é o que o provedor guarda, `local_due_at` é a
   hora que ele joga fora (§3.3.2). Mantê-las separadas é o que permite
   responder "o Google só tem a data, eu tenho a hora" em vez de
   descobrir depois que o dado sumiu. `local_fields_reason` guarda o
   texto do aviso, para a UI não recalcular a explicação.

2. **`task_event_links` referencia por (kind, id), não por FK.** A
   tarefa pode estar em `local_tasks` ou em `tasks_shadow`, e o evento
   em `local_events` ou `events_shadow` — quatro combinações. FK para
   duas tabelas alternativas não existe em SQL; o par discriminado é a
   forma honesta. O preço é que a integridade dessa ponta é da
   aplicação, e está concentrada em `task_store._resolve_ref`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_m4_tasks"
down_revision: str | None = "0007_m3_study"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UID = "NULLIF(current_setting('app.user_id', true), '')::uuid"


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE core.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE core.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY own_rows ON core.{table}
          USING (user_id = {_UID})
          WITH CHECK (user_id = {_UID})
        """
    )


def upgrade() -> None:
    # -----------------------------------------------------------
    # Lista nativa (§3.3.3)
    # -----------------------------------------------------------
    op.create_table(
        "local_task_lists",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("color", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["core.users.id"], ondelete="CASCADE"
        ),
        schema="core",
    )
    # Uma lista default por usuário, garantido pelo banco. Duas
    # defaults fariam a escrita cair em lugar imprevisível.
    op.execute(
        """
        CREATE UNIQUE INDEX local_task_lists_one_default_idx
          ON core.local_task_lists (user_id)
          WHERE is_default
        """
    )
    _rls("local_task_lists")

    op.create_table(
        "local_tasks",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "local_task_list_id", sa.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        # Os dois campos de prazo, pelo motivo em ports/tasks.py: "até
        # sexta" e "sexta às 15h" são compromissos diferentes.
        # `due_date` é derivado de `due_at` quando este existe, e a
        # coerência é garantida na porta.
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default="open"
        ),
        sa.Column(
            "completed_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("priority", sa.Text(), nullable=True),
        sa.Column(
            "labels",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column("parent_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("recurrence", sa.Text(), nullable=True),
        sa.Column("estimated_min", sa.Integer(), nullable=True),
        # Posição explícita, para `REORDER` (§5.7). Sem ela, a ordem
        # seria a de criação e "organiza isso" não teria onde gravar o
        # resultado.
        sa.Column(
            "position", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "created_by_age",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("plan_item_id", sa.UUID(as_uuid=True), nullable=True),
        # Controle de concorrência otimista, igual a `local_events`:
        # é o `etag` que a porta expõe.
        sa.Column(
            "version", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["core.users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["local_task_list_id"],
            ["core.local_task_lists.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["core.local_tasks.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "status IN ('open', 'done')", name="local_tasks_status_valid"
        ),
        sa.CheckConstraint(
            "priority IS NULL OR priority IN "
            "('low', 'normal', 'high', 'urgent')",
            name="local_tasks_priority_valid",
        ),
        # A invariante da porta, imposta também pelo banco: concluída
        # sem data de conclusão impediria qualquer relatório (§5.9.9).
        sa.CheckConstraint(
            "(status = 'done') = (completed_at IS NOT NULL)",
            name="local_tasks_completion_coherent",
        ),
        # Sobre a coerência entre `due_date` e `due_at`: ela é garantida
        # em `Task.__post_init__` e NÃO há CHECK aqui, de propósito. O
        # dia de um instante depende do fuso do usuário, e um CHECK só
        # pode comparar contra um fuso fixo — `(due_at AT TIME ZONE
        # 'UTC')::date` rejeitaria uma tarefa legítima das 22h em
        # São Paulo, que em UTC já é o dia seguinte. Um CHECK que erra
        # nesse caso é pior que nenhum.
        sa.CheckConstraint(
            "due_at IS NULL OR due_date IS NOT NULL",
            name="local_tasks_due_date_present_with_time",
        ),
        sa.CheckConstraint(
            "estimated_min IS NULL OR estimated_min > 0",
            name="local_tasks_estimated_positive",
        ),
        schema="core",
    )
    # A consulta mais frequente do produto: "o que está aberto e vence
    # até quando" (§5.9.5).
    op.execute(
        """
        CREATE INDEX local_tasks_open_due_idx
          ON core.local_tasks (user_id, due_date NULLS LAST, position)
          WHERE deleted_at IS NULL AND status = 'open'
        """
    )
    op.create_index(
        "local_tasks_list_idx",
        "local_tasks",
        ["local_task_list_id", "position"],
        schema="core",
    )
    _rls("local_tasks")

    # -----------------------------------------------------------
    # Conexão externa — sem coluna de provedor
    # -----------------------------------------------------------
    op.create_table(
        "task_connections",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_key", sa.Text(), nullable=False),
        # Forma do blob varia por provedor; só o adaptador sabe ler.
        sa.Column("encrypted_credentials", sa.LargeBinary(), nullable=True),
        sa.Column(
            "credential_expires_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("account_label", sa.Text(), nullable=True),
        # Cache do que o adaptador declarou, para a UI avisar sobre
        # degradação sem instanciar o provedor (§3.3.2).
        sa.Column("capabilities", sa.JSON(), nullable=True),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default="active"
        ),
        sa.Column(
            "last_synced_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["core.users.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "user_id", "provider_key", "account_label",
            name="task_connections_unique_account",
        ),
        schema="core",
    )
    _rls("task_connections")

    op.create_table(
        "task_list_selections",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("external_list_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "read_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "write_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "is_write_target",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        # Privacidade: o título pode não ir para o LLM (§9.3).
        sa.Column(
            "send_titles_to_llm",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("color", sa.Text(), nullable=True),
        # Cursor opaco de sync, junto da seleção. O calendário tem
        # tabela separada por razões históricas; aqui uma coluna basta,
        # e o conteúdo segue tão opaco quanto.
        sa.Column("cursor_opaque", sa.Text(), nullable=True),
        sa.Column(
            "cursor_issued_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "last_full_resync_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["core.users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["core.task_connections.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "connection_id", "external_list_id",
            name="task_list_selections_unique",
        ),
        schema="core",
    )
    _rls("task_list_selections")

    # -----------------------------------------------------------
    # Sombra de tarefa externa + o que o provedor NÃO guarda (§3.3.2)
    # -----------------------------------------------------------
    op.create_table(
        "tasks_shadow",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("external_list_id", sa.Text(), nullable=False),
        sa.Column("external_task_id", sa.Text(), nullable=False),
        # O que o provedor guarda:
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default="open"
        ),
        sa.Column(
            "completed_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("parent_external_id", sa.Text(), nullable=True),
        # O que ele NÃO guarda e o Age preserva (§3.3.2). Prefixo
        # `local_` deliberado: lendo a tabela fica óbvio que esse valor
        # não existe do lado do provedor.
        sa.Column("local_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("local_estimated_min", sa.Integer(), nullable=True),
        sa.Column("local_priority", sa.Text(), nullable=True),
        sa.Column("local_labels", sa.JSON(), nullable=True),
        sa.Column("local_recurrence", sa.Text(), nullable=True),
        # Texto do aviso, para a UI não recalcular a explicação.
        sa.Column("local_fields_reason", sa.Text(), nullable=True),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("created_by_age", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("plan_item_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["core.users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["core.task_connections.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "connection_id", "external_task_id", name="tasks_shadow_unique"
        ),
        sa.CheckConstraint(
            "status IN ('open', 'done')", name="tasks_shadow_status_valid"
        ),
        schema="core",
    )
    op.execute(
        """
        CREATE INDEX tasks_shadow_open_due_idx
          ON core.tasks_shadow (user_id, due_date NULLS LAST)
          WHERE deleted_at IS NULL AND status = 'open'
        """
    )
    _rls("tasks_shadow")

    # -----------------------------------------------------------
    # Vínculo tarefa ↔ evento (§5.7.1)
    # -----------------------------------------------------------
    op.create_table(
        "task_event_links",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        # Referência discriminada: a tarefa pode estar em duas tabelas e
        # o evento em outras duas. Ver o docstring do módulo.
        sa.Column("task_ref_kind", sa.Text(), nullable=False),
        sa.Column("task_ref_id", sa.Text(), nullable=False),
        sa.Column("event_ref_kind", sa.Text(), nullable=False),
        sa.Column("event_ref_id", sa.Text(), nullable=False),
        sa.Column("created_by_plan_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Soft delete: desfazer um par precisa saber que ele existiu.
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["core.users.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "task_ref_kind IN ('local_tasks', 'tasks_shadow')",
            name="task_event_links_task_kind_valid",
        ),
        sa.CheckConstraint(
            "event_ref_kind IN ('local_events', 'events_shadow')",
            name="task_event_links_event_kind_valid",
        ),
        schema="core",
    )
    # Par único enquanto vivo. Reservar duas vezes o mesmo horário para
    # a mesma tarefa é sempre erro — e sem este índice o undo não
    # saberia qual linha desfazer.
    op.execute(
        """
        CREATE UNIQUE INDEX task_event_links_pair_idx
          ON core.task_event_links
             (task_ref_kind, task_ref_id, event_ref_kind, event_ref_id)
          WHERE deleted_at IS NULL
        """
    )
    # "Essa tarefa tem tempo reservado?" — a pergunta de §5.9.5.
    op.execute(
        """
        CREATE INDEX task_event_links_by_task_idx
          ON core.task_event_links (user_id, task_ref_kind, task_ref_id)
          WHERE deleted_at IS NULL
        """
    )
    op.execute(
        """
        CREATE INDEX task_event_links_by_event_idx
          ON core.task_event_links (user_id, event_ref_kind, event_ref_id)
          WHERE deleted_at IS NULL
        """
    )
    _rls("task_event_links")

    # -----------------------------------------------------------
    # `plan_items` ganha a ordem pedida por um REORDER (§5.7)
    # -----------------------------------------------------------
    # Fica em coluna própria e não dentro de `after` porque reordenar
    # não muda campo nenhum da tarefa — muda a relação ENTRE elas, que
    # não cabe numa entidade só. Enfiar a lista no `after` faria o
    # payload de um item significar duas coisas diferentes conforme a
    # operação.
    op.add_column(
        "plan_items",
        sa.Column("reorder_ids", sa.JSON(), nullable=True),
        schema="core",
    )

    # -----------------------------------------------------------
    # `plan_undo_log` precisa saber QUAL porta reverter
    # -----------------------------------------------------------
    # Sem esta coluna o undo chamaria `delete_event` com o id de uma
    # tarefa. O erro seria silencioso no caso do calendário nativo —
    # `delete_event` é idempotente e não reclama de id inexistente —
    # então o undo relataria sucesso e a tarefa continuaria lá.
    #
    # É o tipo de bug que só aparece quando alguém tenta desfazer, que
    # é justamente o momento em que a pessoa mais precisa confiar no
    # sistema.
    op.add_column(
        "plan_undo_log",
        sa.Column(
            "target", sa.Text(), nullable=False, server_default="event"
        ),
        schema="core",
    )
    op.create_check_constraint(
        "plan_undo_log_target_valid",
        "plan_undo_log",
        "target IN ('event', 'task')",
        schema="core",
    )

    # -----------------------------------------------------------
    # Destino: corrigindo o que o M1 deixou pela metade (§5.7.2)
    # -----------------------------------------------------------
    # `user_preferences.default_target` (camada 1) e
    # `conversations.target_default` (camada 2) JÁ EXISTEM, criadas em
    # 0002 quando as tabelas nasceram. Falta a lista de escrita:
    op.add_column(
        "user_preferences",
        sa.Column("default_write_task_list_id", sa.Text(), nullable=True),
        schema="core",
    )

    # E falta corrigir um erro de modelagem do M1, que só aparece agora
    # que a camada 2 vai ser usada de verdade:
    #
    # `conversations.target_default` nasceu NOT NULL DEFAULT 'event'.
    # Com isso a conversa SEMPRE tem opinião, e "herdar da conta" não
    # tem como ser representado — se o usuário mudar a preferência da
    # conta para `both`, todas as conversas existentes continuam presas
    # em `event`, sem nunca ter escolhido isso. A camada 2 venceria a
    # camada 1 por acidente de default, que é precisamente o que a
    # tabela de precedência de §5.7.2 não quer.
    #
    # NULL passa a significar "herda da conta", que é um estado
    # distinto de qualquer valor. A migração de dados assume que quem
    # está em 'event' hoje nunca escolheu (era o default), e por isso
    # volta a herdar.
    op.execute("ALTER TABLE core.conversations ALTER COLUMN target_default DROP NOT NULL")
    op.execute(
        "ALTER TABLE core.conversations ALTER COLUMN target_default DROP DEFAULT"
    )
    op.execute(
        "UPDATE core.conversations SET target_default = NULL "
        "WHERE target_default = 'event'"
    )

    # -----------------------------------------------------------
    # Sobre GRANTs: não há nenhum aqui, e isso é correto.
    # -----------------------------------------------------------
    # `init-db.sql` (§11.2) define ALTER DEFAULT PRIVILEGES FOR ROLE
    # age_owner IN SCHEMA core, então toda tabela criada por esta
    # migração já nasce com `core_api` podendo escrever e `ai_worker`
    # podendo apenas ler. Repetir o GRANT aqui funcionaria, mas criaria
    # duas fontes de verdade para a mesma política — e foi assim que o
    # M1 produziu um GRANT certo com código errado.
    #
    # A assimetria importa e vale registrar: o worker LÊ tarefa para
    # montar contexto e nunca escreve. Escrever é ato que exige
    # aprovação e passa pelo `PlanExecutor` no core-api (§3.4). Quem
    # impõe isso é o Postgres, não disciplina de revisão.


def downgrade() -> None:
    op.drop_constraint(
        "plan_undo_log_target_valid",
        "plan_undo_log",
        schema="core",
        type_="check",
    )
    op.drop_column("plan_undo_log", "target", schema="core")
    op.drop_column("plan_items", "reorder_ids", schema="core")
    op.execute(
        "UPDATE core.conversations SET target_default = 'event' "
        "WHERE target_default IS NULL"
    )
    op.execute(
        "ALTER TABLE core.conversations "
        "ALTER COLUMN target_default SET DEFAULT 'event'"
    )
    op.execute(
        "ALTER TABLE core.conversations "
        "ALTER COLUMN target_default SET NOT NULL"
    )
    op.drop_column(
        "user_preferences", "default_write_task_list_id", schema="core"
    )

    for table in (
        "task_event_links",
        "tasks_shadow",
        "task_list_selections",
        "task_connections",
        "local_tasks",
        "local_task_lists",
    ):
        op.drop_table(table, schema="core")
