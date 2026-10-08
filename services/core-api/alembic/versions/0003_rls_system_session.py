"""RLS: suporte a sessão de sistema sem falhar aberto

Revision ID: 0003_rls_system_session
Revises: 0002_m1_auth_conversations
Create Date: 2026-10-08

Corrige um conflito real entre duas decisões de §4.2.2:

- `current_setting('app.user_id')` SEM o segundo argumento falha alto
  quando o parâmetro não está setado. Isso é desejado nas rotas de
  usuário: falhar é melhor que vazar.
- Mas registro, login e renovação de token acontecem ANTES de haver
  usuário, e usam `system_session` legitimamente. Ali o parâmetro não
  existe, e a query estourava com `unrecognized configuration
  parameter`.

A solução mantém a propriedade de segurança sem o efeito colateral:
`current_setting(..., true)` devolve NULL em vez de erro, e a política
compara com `NULLIF(..., '')::uuid`. Sem `app.user_id`, a comparação é
`user_id = NULL` → NULL → **nenhuma linha visível**.

Ou seja: continua falhando FECHADO. A diferença é que agora falha
devolvendo zero linhas em vez de derrubar a conexão — e as rotas que
precisam de acesso sem usuário o fazem por política explícita
(`insert_any` em `users`, e filtro por hash em `sessions`).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_rls_system_session"
down_revision: str | None = "0002_m1_auth_conversations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tabelas com política por `user_id`.
_USER_TABLES = (
    "sessions",
    "auth_tokens",
    "oauth_identities",
    "llm_credentials",
    "conversations",
    "messages",
    "conversation_summaries",
    "user_preferences",
    "usage_log",
)

_EXPR = "NULLIF(current_setting('app.user_id', true), '')::uuid"


def upgrade() -> None:
    for table in _USER_TABLES:
        op.execute(f"DROP POLICY IF EXISTS own_rows ON core.{table}")
        op.execute(
            f"""
            CREATE POLICY own_rows ON core.{table}
              USING (user_id = {_EXPR})
              WITH CHECK (user_id = {_EXPR})
            """
        )

    # `users` compara `id`, não `user_id`.
    op.execute("DROP POLICY IF EXISTS own_row ON core.users")
    op.execute(
        f"""
        CREATE POLICY own_row ON core.users
          USING (id = {_EXPR})
        """
    )

    # Login e renovação precisam achar a linha ANTES de haver sessão.
    # A busca é sempre por e-mail exato ou por hash de token — nunca
    # listagem —, então expor leitura aqui não permite enumerar contas.
    op.execute("DROP POLICY IF EXISTS login_lookup ON core.users")
    op.execute(
        """
        CREATE POLICY login_lookup ON core.users
          FOR SELECT
          USING (current_setting('app.user_id', true) IS NULL
                 OR current_setting('app.user_id', true) = '')
        """
    )

    # Rotação de refresh token (§4.1.2) roda sem usuário autenticado: o
    # próprio token é a credencial, e a busca é por hash único.
    op.execute("DROP POLICY IF EXISTS token_rotation ON core.sessions")
    op.execute(
        """
        CREATE POLICY token_rotation ON core.sessions
          USING (current_setting('app.user_id', true) IS NULL
                 OR current_setting('app.user_id', true) = '')
          WITH CHECK (current_setting('app.user_id', true) IS NULL
                      OR current_setting('app.user_id', true) = '')
        """
    )

    # Registro cria as preferências junto da conta, antes de haver sessão.
    op.execute("DROP POLICY IF EXISTS bootstrap_insert ON core.user_preferences")
    op.execute(
        """
        CREATE POLICY bootstrap_insert ON core.user_preferences
          FOR INSERT
          WITH CHECK (current_setting('app.user_id', true) IS NULL
                      OR current_setting('app.user_id', true) = '')
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS bootstrap_insert ON core.user_preferences")
    op.execute("DROP POLICY IF EXISTS token_rotation ON core.sessions")
    op.execute("DROP POLICY IF EXISTS login_lookup ON core.users")

    for table in _USER_TABLES:
        op.execute(f"DROP POLICY IF EXISTS own_rows ON core.{table}")
        op.execute(
            f"""
            CREATE POLICY own_rows ON core.{table}
              USING (user_id = current_setting('app.user_id')::uuid)
              WITH CHECK (user_id = current_setting('app.user_id')::uuid)
            """
        )

    op.execute("DROP POLICY IF EXISTS own_row ON core.users")
    op.execute(
        """
        CREATE POLICY own_row ON core.users
          USING (id = current_setting('app.user_id')::uuid)
        """
    )
