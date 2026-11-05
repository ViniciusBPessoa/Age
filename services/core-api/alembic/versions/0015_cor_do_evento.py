"""Cor de categoria no evento local.

Revision ID: 0015_cor_do_evento
Revises: 0014_anexo_de_pdf

A agenda repaginada deixa a pessoa organizar a semana por cor, como
nas cores de evento do Google Agenda: aula, trabalho, saúde, lazer.

**Nome, não hex.** A coluna guarda um dos onze nomes da paleta fixa
(`EVENT_COLORS` em `app/ports/calendar.py`). O tema claro e o escuro
pintam o mesmo nome com tons diferentes; um hex livre escolhido no tema
escuro ficaria ilegível no claro. O CHECK é a segunda barreira, depois
do `Literal` da rota, para que nenhum caminho de escrita (o executor de
planos, um script) grave um valor que a tela não sabe desenhar.

Nulo é "sem cor": o evento usa a cor do seu tipo (fixo, flexível).
"""

from __future__ import annotations

from alembic import op

revision: str = "0015_cor_do_evento"
down_revision: str | None = "0014_anexo_de_pdf"
branch_labels = None
depends_on = None

CORES = (
    "tomate",
    "tangerina",
    "banana",
    "salvia",
    "manjericao",
    "pavao",
    "mirtilo",
    "lavanda",
    "uva",
    "flamingo",
    "grafite",
)


def upgrade() -> None:
    op.execute("ALTER TABLE core.local_events ADD COLUMN color text")
    lista = ", ".join(f"'{c}'" for c in CORES)
    op.execute(
        "ALTER TABLE core.local_events ADD CONSTRAINT ck_local_events_color "
        f"CHECK (color IS NULL OR color IN ({lista}))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE core.local_events DROP CONSTRAINT IF EXISTS ck_local_events_color"
    )
    op.execute("ALTER TABLE core.local_events DROP COLUMN IF EXISTS color")
