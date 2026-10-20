"""Anexos de imagem nas mensagens

Revision ID: 0013_anexos_de_imagem
Revises: 0012_custo_por_mensagem
Create Date: 2026-10-20

Uma tabela, e a decisão que ela registra é onde os BYTES ficam.

## Os bytes vão para o banco, não para o disco

O plano dizia volume no disco + caminho na tabela. Mudei depois de olhar
o que isso custa de verdade aqui, e a razão é consistência, não
preguiça:

- Arquivo no disco e linha no banco não compartilham transação. Upload
  que falha no meio deixa órfão nos dois sentidos — linha sem arquivo
  (erro ao ler, depois) ou arquivo sem linha (lixo que ninguém coleta).
  Com os bytes na linha, o `ROLLBACK` resolve os dois.
- `DELETE FROM core.messages` já apaga anexo em cascata. Com arquivo
  fora, apagar conversa exigiria varredura de disco, e §9 (apagar os
  dados da conta) passaria a ter duas metades que podem divergir.
- RLS vale para a linha. Arquivo no disco não tem policy: quem errar o
  caminho numa rota lê anexo de outra conta, e isso é exatamente o que
  §4.2 impede no banco.
- O volume: 5 MB por imagem, 4 por mensagem. Mesmo 1000 mensagens com
  anexo dão ~20 GB no pior caso teórico, e o caso real é uma foto de
  celular de ~2 MB de vez em quando. Postgres guarda `bytea` grande em
  TOAST, fora da página principal, então a tabela de mensagens não fica
  mais lenta por isso.

O que isto NÃO escala: muitos usuários com muitas imagens, ou vídeo. Aí
o lugar certo é object storage (S3/MinIO) com URL assinada, e a coluna
`storage_key` abaixo é o gancho para essa migração — fica `NULL`
enquanto os bytes estiverem aqui. Registro o limite agora para a decisão
ser revista por dado, não por surpresa.

## `sha256` e deduplicação

Guardado, mas SEM unique: a mesma imagem mandada em duas conversas é
dois anexos, porque apagar uma conversa não pode apagar o anexo da
outra. O hash serve para detectar reenvio do mesmo arquivo no cliente e
para conferir integridade — não para economizar espaço, que seria
economia errada neste caso.

## `media_type` é CHECK, não enum

Os quatro que o DeepSeek aceita. Um CHECK é alterável por migration sem
`ALTER TYPE`, e o conjunto muda quando o provedor muda. O valor aqui é o
tipo DETECTADO pelos bytes (`domain/imagem.py`), nunca o que o cliente
declarou.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_anexos_de_imagem"
down_revision: str | None = "0012_custo_por_mensagem"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UID = "NULLIF(current_setting('app.user_id', true), '')::uuid"

# Os quatro formatos que o provedor aceita (doc oficial, out/2026).
_TIPOS = ("image/jpeg", "image/png", "image/gif", "image/webp")


def upgrade() -> None:
    op.create_table(
        "message_attachments",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("ext.gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        # O anexo nasce ANTES da mensagem: o upload acontece enquanto a
        # pessoa ainda está escrevendo, e só o envio cria a mensagem.
        # Por isso nullable, e por isso existe `created_at` — anexo que
        # nunca foi enviado é órfão e precisa de varredura.
        sa.Column("message_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("conversation_id", sa.UUID(as_uuid=True), nullable=True),
        # Nome original, só para mostrar na tela. NUNCA usado para
        # decidir tipo, nem para montar caminho de arquivo.
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        # Os bytes. `NULL` quando (e se) migrarmos para object storage.
        sa.Column("data", sa.LargeBinary(), nullable=True),
        # Gancho para object storage futuro. Ver cabeçalho.
        sa.Column("storage_key", sa.Text(), nullable=True),
        # Ordem dentro da mensagem: "compare a primeira com a segunda"
        # só faz sentido se a ordem sobreviver ao reload.
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["core.users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["message_id"], ["core.messages.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["core.conversations.id"], ondelete="CASCADE"
        ),
        # Os bytes estão aqui OU numa chave de storage, nunca em lugar
        # nenhum: uma linha sem os dois é um anexo que não existe, e
        # descobrir isso na hora de montar o prompt é tarde.
        sa.CheckConstraint(
            "(data IS NOT NULL) OR (storage_key IS NOT NULL)",
            name="ck_attachment_tem_conteudo",
        ),
        sa.CheckConstraint(
            "media_type IN ('image/jpeg', 'image/png', 'image/gif', 'image/webp')",
            name="ck_attachment_media_type",
        ),
        # O limite também no banco. A rota já valida, mas a rota é uma
        # linha de código e a constraint é a invariante: 5 MB.
        sa.CheckConstraint(
            "size_bytes > 0 AND size_bytes <= 5242880",
            name="ck_attachment_tamanho",
        ),
        schema="core",
    )

    # Montar o prompt busca por mensagem, na ordem.
    op.create_index(
        "ix_attachments_message",
        "message_attachments",
        ["message_id", "position"],
        schema="core",
    )
    # A varredura de órfãos busca por "sem mensagem e velho".
    op.create_index(
        "ix_attachments_orfaos",
        "message_attachments",
        ["created_at"],
        schema="core",
        postgresql_where=sa.text("message_id IS NULL"),
    )

    op.execute("ALTER TABLE core.message_attachments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE core.message_attachments FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY own_rows ON core.message_attachments
          USING (user_id = {_UID})
          WITH CHECK (user_id = {_UID})
        """
    )

    # Sem GRANT: `init-db.sql` (§11.2) tem ALTER DEFAULT PRIVILEGES FOR
    # ROLE age_owner, então `core_api` já recebe CRUD e `ai_worker` já
    # recebe SELECT — que é exatamente o que cada um precisa aqui: o
    # core-api grava no upload, o worker lê para montar o prompt.


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS own_rows ON core.message_attachments")
    op.drop_index("ix_attachments_orfaos", "message_attachments", schema="core")
    op.drop_index("ix_attachments_message", "message_attachments", schema="core")
    op.drop_table("message_attachments", schema="core")
