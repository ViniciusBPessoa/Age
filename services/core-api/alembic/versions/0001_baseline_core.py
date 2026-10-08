"""baseline do schema core: users + helper de RLS

Revision ID: 0001_baseline_core
Revises:
Create Date: 2026-10-07

Esta migration cria o mínimo para o M0 provar a fronteira de segurança:
`core.users` com RLS ativo e FORCE. As tabelas de produto (conversas,
planos, agenda) entram no M1 e M2.

A função `core.enable_rls` existe para que toda migration futura ative a
política do mesmo jeito. ARQUITETURA.md §4.2.1: sem `FORCE ROW LEVEL
SECURITY`, o dono da tabela ignora a política e a proteção não vale nada.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_baseline_core"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Helper reutilizado por toda tabela com user_id.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION core.enable_rls(target regclass)
        RETURNS void LANGUAGE plpgsql AS $$
        BEGIN
          EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', target);
          -- FORCE é o que faz a política valer até para o dono.
          EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', target);
          EXECUTE format(
            'CREATE POLICY own_rows ON %s USING '
            '(user_id = current_setting(''app.user_id'')::uuid)',
            target
          );
        END $$;
        """
    )

    op.create_table(
        "users",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column(
            "timezone",
            sa.Text(),
            nullable=False,
            server_default="America/Sao_Paulo",
        ),
        sa.Column("locale", sa.Text(), nullable=False, server_default="pt-BR"),
        sa.Column(
            "email_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("role", sa.Text(), nullable=False, server_default="user"),
        sa.Column("onboarding_step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_login_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("role IN ('user','admin')", name="users_role_valid"),
        schema="core",
    )

    # citext: e-mail é case-insensitive por natureza. Comparar com LOWER()
    # em toda query é o caminho para esquecer em uma delas.
    op.execute("ALTER TABLE core.users ALTER COLUMN email TYPE ext.citext")
    op.create_index(
        "users_email_unique",
        "users",
        ["email"],
        unique=True,
        schema="core",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # `users` é o caso especial: a política compara `id`, não `user_id`.
    op.execute("ALTER TABLE core.users ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE core.users FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY own_row ON core.users
          USING (id = current_setting('app.user_id')::uuid)
        """
    )
    # Registro e login acontecem antes de haver sessão de usuário, então
    # a role de API precisa poder inserir e ler por e-mail. A política de
    # escrita é separada da de leitura de propósito.
    op.execute(
        """
        CREATE POLICY insert_any ON core.users
          FOR INSERT WITH CHECK (true)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS insert_any ON core.users")
    op.execute("DROP POLICY IF EXISTS own_row ON core.users")
    op.drop_index("users_email_unique", table_name="users", schema="core")
    op.drop_table("users", schema="core")
    op.execute("DROP FUNCTION IF EXISTS core.enable_rls(regclass)")
