"""Ferramentas expostas ao modelo.

ARQUITETURA.md §5.2. A divisão em três categorias é o que torna a
escrita segura:

- **Leitura** — executam de verdade, na hora.
- **Escrita** — NÃO executam; acumulam item no `PlanDiff` e devolvem
  `{"staged": true}`. O modelo sabe que registrou a intenção, não que
  executou — e o system prompt diz isso explicitamente, para ele não
  afirmar "já agendei" quando nada foi agendado (§5.9.11).
- **Registro de fato** — executam, mas não tocam a agenda. "Pulei o
  estudo de ontem" é fato sobre o passado, não proposta de mudança;
  tratar como plano forçaria o usuário a "aprovar" que faltou.
"""

from app.domain.tools.registry import (
    ToolCategory,
    ToolContext,
    build_tool_specs,
    execute_tool,
    register_tool,
)

__all__ = [
    "ToolCategory",
    "ToolContext",
    "build_tool_specs",
    "execute_tool",
    "register_tool",
]
