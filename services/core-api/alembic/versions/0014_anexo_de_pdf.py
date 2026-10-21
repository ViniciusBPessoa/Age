"""Anexo de PDF: o tipo novo e o texto extraído

Revision ID: 0014_anexo_de_pdf
Revises: 0013_anexos_de_imagem
Create Date: 2026-10-21

Duas mudanças na mesma tabela, e a segunda é a que importa.

## `application/pdf` no CHECK

A migration 0013 restringia `media_type` aos quatro formatos de imagem
que o provedor aceita. O PDF entra na lista, mas por um caminho
diferente: a imagem vai para o modelo como imagem, o PDF vai como
**texto extraído**.

## `extracted_text`

O texto sai do PDF **no upload**, não na hora de montar o prompt.

Extrair no worker pareceria mais limpo — a extração é transformação de
conteúdo, não validação —, mas erra no que importa: PDF escaneado não
tem texto, e descobrir isso no worker faz a pessoa esperar um turno
inteiro para ouvir "não entendi o anexo", já cobrado. Extraindo aqui, a
recusa é imediata e o arquivo ainda está na tela.

O efeito colateral bom: o worker não precisa de `pypdf`. Ele lê uma
coluna de texto, e o schema `ai` continua sem saber o que é PDF.

Guardamos **os dois**: os bytes originais em `data` (para a pessoa
reabrir o que mandou) e o texto em `extracted_text` (para o prompt).
Guardar só o texto perderia o original; guardar só os bytes obrigaria a
reextrair a cada turno, com `pypdf` no worker e o mesmo trabalho
repetido.

`page_count` é para a tela dizer "12 páginas" sem reabrir o arquivo, e
para avisar quando o documento foi cortado no teto de 50.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_anexo_de_pdf"
down_revision: str | None = "0013_anexos_de_imagem"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TIPOS_COM_PDF = (
    "'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'application/pdf'"
)
_TIPOS_SO_IMAGEM = "'image/jpeg', 'image/png', 'image/gif', 'image/webp'"


def upgrade() -> None:
    op.add_column(
        "message_attachments",
        # `NULL` em imagem, e é a distinção que o worker usa para saber
        # se manda como imagem ou como texto. Vazio (`''`) seria "PDF
        # sem texto", que a rota já recusa — então `NULL` é só imagem.
        sa.Column("extracted_text", sa.Text(), nullable=True),
        schema="core",
    )
    op.add_column(
        "message_attachments",
        sa.Column("page_count", sa.Integer(), nullable=True),
        schema="core",
    )

    op.drop_constraint(
        "ck_attachment_media_type", "message_attachments", schema="core", type_="check"
    )
    op.create_check_constraint(
        "ck_attachment_media_type",
        "message_attachments",
        f"media_type IN ({_TIPOS_COM_PDF})",
        schema="core",
    )

    # PDF sem texto não serve para nada: foi aceito, gravado, e o worker
    # não terá o que mandar ao modelo. A rota recusa antes, e isto
    # garante que nenhum outro caminho (script, migration de dados)
    # escape — a mesma razão dos outros CHECKs desta tabela.
    op.create_check_constraint(
        "ck_attachment_pdf_tem_texto",
        "message_attachments",
        "media_type <> 'application/pdf' OR extracted_text IS NOT NULL",
        schema="core",
    )


def downgrade() -> None:
    # Os PDFs saem antes de o CHECK voltar a proibi-los: deixar a linha
    # com um `media_type` que a constraint recusa tornaria a migration
    # inaplicável na volta.
    #
    # RLS vale inclusive para o dono (`FORCE ROW LEVEL SECURITY`), e
    # `age_owner` não tem `BYPASSRLS` — sem desligar, este DELETE apaga
    # ZERO linhas e não reclama, e o `create_check_constraint` abaixo
    # falha com um erro que não aponta para a causa.
    op.execute("ALTER TABLE core.message_attachments NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE core.message_attachments DISABLE ROW LEVEL SECURITY")
    try:
        op.execute(
            "DELETE FROM core.message_attachments WHERE media_type = 'application/pdf'"
        )
    finally:
        op.execute("ALTER TABLE core.message_attachments ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE core.message_attachments FORCE ROW LEVEL SECURITY")

    op.drop_constraint(
        "ck_attachment_pdf_tem_texto",
        "message_attachments",
        schema="core",
        type_="check",
    )
    op.drop_constraint(
        "ck_attachment_media_type", "message_attachments", schema="core", type_="check"
    )
    op.create_check_constraint(
        "ck_attachment_media_type",
        "message_attachments",
        f"media_type IN ({_TIPOS_SO_IMAGEM})",
        schema="core",
    )
    op.drop_column("message_attachments", "page_count", schema="core")
    op.drop_column("message_attachments", "extracted_text", schema="core")
