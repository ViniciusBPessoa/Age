"""RLS: bootstrap de preferências com INSERT ... RETURNING

Revision ID: 0004_rls_bootstrap_fix
Revises: 0003_rls_system_session
Create Date: 2026-10-08

A migration 0003 criou `bootstrap_insert` apenas `FOR INSERT`, e isso
não bastou por um detalhe do Postgres que é fácil não antecipar:

**`INSERT ... RETURNING` exige política de SELECT sobre a linha nova.**
O SQLAlchemy sempre acrescenta `RETURNING` para preencher os defaults do
servidor, então todo insert via ORM passa por essa checagem. Com
`app.user_id` vazio (sessão de bootstrap), `own_rows` não casava e o
insert era recusado com a mensagem enganosa `new row violates
row-level security policy`.

A correção substitui as duas políticas por uma só, `FOR ALL`, que trata
os dois casos de forma explícita:

  - com `app.user_id` setado  → só as linhas daquele usuário
  - sem `app.user_id`         → acesso de bootstrap

E mantém a propriedade de segurança: o acesso sem usuário só existe nas
sessões que a aplicação abre deliberadamente por `system_session`
(§4.2.2), cujos usos são poucos, nomeados e revisados — registro, login,
rotação de token e jobs de varredura.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_rls_bootstrap_fix"
down_revision: str | None = "0003_rls_system_session"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UID = "NULLIF(current_setting('app.user_id', true), '')::uuid"
_NO_UID = (
    "(current_setting('app.user_id', true) IS NULL "
    "OR current_setting('app.user_id', true) = '')"
)

# Tabelas que a aplicação precisa tocar durante o bootstrap, antes de
# haver sessão de usuário.
_BOOTSTRAP_TABLES = ("user_preferences", "sessions")

# Tabelas de dados puros: só o dono enxerga, sempre.
_STRICT_TABLES = (
    "auth_tokens",
    "oauth_identities",
    "llm_credentials",
    "conversations",
    "messages",
    "conversation_summaries",
    "usage_log",
)


def upgrade() -> None:
    # --- Tabelas de bootstrap: dono OU sessão sem usuário ---
    for table in _BOOTSTRAP_TABLES:
        op.execute(f"DROP POLICY IF EXISTS own_rows ON core.{table}")
        op.execute(f"DROP POLICY IF EXISTS bootstrap_insert ON core.{table}")
        op.execute(f"DROP POLICY IF EXISTS token_rotation ON core.{table}")
        op.execute(
            f"""
            CREATE POLICY own_rows ON core.{table}
              USING (user_id = {_UID} OR {_NO_UID})
              WITH CHECK (user_id = {_UID} OR {_NO_UID})
            """
        )

    # --- Tabelas estritas: sem app.user_id, nenhuma linha ---
    for table in _STRICT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS own_rows ON core.{table}")
        op.execute(
            f"""
            CREATE POLICY own_rows ON core.{table}
              USING (user_id = {_UID})
              WITH CHECK (user_id = {_UID})
            """
        )

    # --- users: dono, mais o lookup de registro/login ---
    op.execute("DROP POLICY IF EXISTS own_row ON core.users")
    op.execute("DROP POLICY IF EXISTS insert_any ON core.users")
    op.execute("DROP POLICY IF EXISTS login_lookup ON core.users")
    op.execute(
        f"""
        CREATE POLICY own_row ON core.users
          USING (id = {_UID} OR {_NO_UID})
          WITH CHECK (id = {_UID} OR {_NO_UID})
        """
    )


def downgrade() -> None:
    for table in (*_BOOTSTRAP_TABLES, *_STRICT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS own_rows ON core.{table}")
        op.execute(
            f"""
            CREATE POLICY own_rows ON core.{table}
              USING (user_id = {_UID})
              WITH CHECK (user_id = {_UID})
            """
        )

    op.execute("DROP POLICY IF EXISTS own_row ON core.users")
    op.execute(
        f"""
        CREATE POLICY own_row ON core.users
          USING (id = {_UID})
        """
    )
    op.execute(
        "CREATE POLICY insert_any ON core.users FOR INSERT WITH CHECK (true)"
    )
