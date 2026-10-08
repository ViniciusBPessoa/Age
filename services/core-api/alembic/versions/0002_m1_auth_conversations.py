"""M1: sessões, credenciais de LLM e conversas

Revision ID: 0002_m1_auth_conversations
Revises: 0001_baseline_core
Create Date: 2026-10-08

ARQUITETURA.md §6.1. Tudo aqui tem RLS com FORCE — a política vale até
para o dono da tabela (§4.2.1).

Nota sobre `sessions`: a tabela guarda só o HASH do refresh token. Vazar
o banco não dá acesso a sessão nenhuma.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_m1_auth_conversations"
down_revision: str | None = "0001_baseline_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rls(table: str) -> None:
    """Ativa RLS com FORCE e política por user_id."""
    op.execute(f"ALTER TABLE core.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE core.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY own_rows ON core.{table}
          USING (user_id = current_setting('app.user_id')::uuid)
          WITH CHECK (user_id = current_setting('app.user_id')::uuid)
        """
    )


def upgrade() -> None:
    # -----------------------------------------------------------
    # Sessões (§4.1.2)
    # -----------------------------------------------------------
    op.create_table(
        "sessions",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        # Só o hash. O token em claro existe apenas no cookie do usuário.
        sa.Column("refresh_token_hash", sa.Text(), nullable=False),
        # Agrupa a cadeia de rotações. Reuso de um token já usado
        # invalida a família inteira — detecção de roubo.
        sa.Column("token_family_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("device_label", sa.Text(), nullable=True),
        sa.Column("ip", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["core.users.id"], ondelete="CASCADE"
        ),
        schema="core",
    )
    op.create_index(
        "sessions_token_hash_idx",
        "sessions",
        ["refresh_token_hash"],
        unique=True,
        schema="core",
    )
    op.create_index(
        "sessions_family_idx", "sessions", ["token_family_id"], schema="core"
    )
    op.create_index(
        "sessions_user_active_idx",
        "sessions",
        ["user_id"],
        schema="core",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    _rls("sessions")

    # -----------------------------------------------------------
    # Tokens de uso único: verificação de e-mail e reset de senha
    # -----------------------------------------------------------
    op.create_table(
        "auth_tokens",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "purpose IN ('verify_email','reset_password')",
            name="auth_tokens_purpose_valid",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["core.users.id"], ondelete="CASCADE"
        ),
        schema="core",
    )
    op.create_index(
        "auth_tokens_hash_idx",
        "auth_tokens",
        ["token_hash"],
        unique=True,
        schema="core",
    )
    _rls("auth_tokens")

    # -----------------------------------------------------------
    # Identidades OAuth de LOGIN (separadas das conexões de dados)
    # -----------------------------------------------------------
    # §4.1: entrar com Google e dar permissão ao Google Calendar são
    # coisas diferentes. Misturá-las é erro comum; aqui são tabelas
    # distintas de propósito.
    op.create_table(
        "oauth_identities",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("provider", "subject", name="oauth_identity_unique"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["core.users.id"], ondelete="CASCADE"
        ),
        schema="core",
    )
    _rls("oauth_identities")

    # -----------------------------------------------------------
    # Credenciais de LLM (§4.3)
    # -----------------------------------------------------------
    op.create_table(
        "llm_credentials",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_key", sa.Text(), nullable=False),
        # Cifrado com AES-256-GCM, AAD = f"llm:{user_id}" (§4.3).
        sa.Column("encrypted_api_key", sa.LargeBinary(), nullable=False),
        # Últimos 4 caracteres, para o usuário reconhecer qual chave é.
        # A chave em claro NUNCA volta pela API.
        sa.Column("key_last4", sa.Text(), nullable=False),
        sa.Column("default_model", sa.Text(), nullable=True),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("last_verified_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "user_id", "provider_key", name="llm_credential_unique"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["core.users.id"], ondelete="CASCADE"
        ),
        schema="core",
    )
    _rls("llm_credentials")

    # -----------------------------------------------------------
    # Conversas (§6.2)
    # -----------------------------------------------------------
    op.create_table(
        "conversations",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        # false depois que o usuário renomeia: o título gerado nunca
        # sobrescreve escolha humana (§6.2.2).
        sa.Column(
            "title_is_auto", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("provider_key", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        # Destino padrão da conversa (§5.7.2). Já existe aqui para o M4
        # não precisar migrar dados de conversa com usuários dentro.
        sa.Column(
            "target_default", sa.Text(), nullable=False, server_default="event"
        ),
        sa.Column("pinned_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("archived_at", sa.TIMESTAMP(timezone=True), nullable=True),
        # Soft delete: janela de 30 dias, e evita violar FK quando um
        # turno está rodando no worker (§6.2.4).
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_message_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "message_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "target_default IN ('event','task','both','ask')",
            name="conversations_target_valid",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["core.users.id"], ondelete="CASCADE"
        ),
        schema="core",
    )
    # Sidebar: agrupamento por recência, só as ativas.
    op.create_index(
        "conversations_user_recent_idx",
        "conversations",
        ["user_id", sa.text("last_message_at DESC NULLS LAST")],
        schema="core",
        postgresql_where=sa.text("deleted_at IS NULL AND archived_at IS NULL"),
    )
    # Job de expurgo: acha rápido o que passou de 30 dias.
    op.create_index(
        "conversations_deleted_idx",
        "conversations",
        ["deleted_at"],
        schema="core",
        postgresql_where=sa.text("deleted_at IS NOT NULL"),
    )
    _rls("conversations")

    # -----------------------------------------------------------
    # Mensagens
    # -----------------------------------------------------------
    op.create_table(
        "messages",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_calls", sa.JSON(), nullable=True),
        sa.Column("tool_call_id", sa.Text(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        # Quem gerou esta mensagem. Ao reler a conversa, o usuário vê
        # qual modelo disse o quê (§3.1.3).
        sa.Column("provider_key", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "role IN ('system','user','assistant','tool')",
            name="messages_role_valid",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["core.conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["core.users.id"], ondelete="CASCADE"
        ),
        schema="core",
    )
    op.create_index(
        "messages_conversation_idx",
        "messages",
        ["conversation_id", "created_at"],
        schema="core",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    # Busca full-text na sidebar (§8.1). `simple` em vez de `portuguese`
    # de propósito: stemming em português atrapalha busca por nome
    # próprio e sigla, que é o caso comum aqui.
    op.execute(
        """
        CREATE INDEX messages_content_fts_idx ON core.messages
          USING gin (to_tsvector('simple', coalesce(content, '')))
        """
    )
    _rls("messages")

    # -----------------------------------------------------------
    # Resumo de conversa longa (§3.1.3, truncamento por context_window)
    # -----------------------------------------------------------
    op.create_table(
        "conversation_summaries",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("up_to_message_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["core.conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["core.users.id"], ondelete="CASCADE"
        ),
        schema="core",
    )
    _rls("conversation_summaries")

    # -----------------------------------------------------------
    # Preferências (§6.1) — criadas aqui, usadas a partir do M2
    # -----------------------------------------------------------
    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("working_hours", sa.JSON(), nullable=True),
        sa.Column("blocked_windows", sa.JSON(), nullable=True),
        sa.Column("preferred_study_times", sa.JSON(), nullable=True),
        sa.Column("session_min", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("session_max", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("break_min", sa.Integer(), nullable=False, server_default="10"),
        sa.Column(
            "max_hours_per_day", sa.Integer(), nullable=False, server_default="6"
        ),
        sa.Column("buffer_min", sa.Integer(), nullable=False, server_default="15"),
        sa.Column(
            "autonomy_level", sa.Text(), nullable=False, server_default="propose"
        ),
        sa.Column(
            "spaced_repetition_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("scoring_weights", sa.JSON(), nullable=True),
        sa.Column("default_write_calendar_id", sa.Text(), nullable=True),
        sa.Column(
            "default_target", sa.Text(), nullable=False, server_default="event"
        ),
        # Modo privado (§9.3): envia "ocupado 14:00-15:30" em vez do título.
        sa.Column(
            "private_mode", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "autonomy_level IN ('propose','auto_create','auto_all')",
            name="prefs_autonomy_valid",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["core.users.id"], ondelete="CASCADE"
        ),
        schema="core",
    )
    op.execute("ALTER TABLE core.user_preferences ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE core.user_preferences FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY own_rows ON core.user_preferences
          USING (user_id = current_setting('app.user_id')::uuid)
          WITH CHECK (user_id = current_setting('app.user_id')::uuid)
        """
    )

    # -----------------------------------------------------------
    # Uso e custo (§12)
    # -----------------------------------------------------------
    op.create_table(
        "usage_log",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
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
        sa.ForeignKeyConstraint(
            ["user_id"], ["core.users.id"], ondelete="CASCADE"
        ),
        schema="core",
    )
    op.create_index(
        "usage_log_user_date_idx",
        "usage_log",
        ["user_id", sa.text("created_at DESC")],
        schema="core",
    )
    _rls("usage_log")


def downgrade() -> None:
    for table in (
        "usage_log",
        "user_preferences",
        "conversation_summaries",
        "messages",
        "conversations",
        "llm_credentials",
        "oauth_identities",
        "auth_tokens",
        "sessions",
    ):
        op.execute(f"DROP POLICY IF EXISTS own_rows ON core.{table}")
        op.drop_table(table, schema="core")
