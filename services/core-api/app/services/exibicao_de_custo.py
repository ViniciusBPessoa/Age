"""Se valores em dinheiro aparecem para uma conta.

Esta é a ÚNICA função que responde essa pergunta no backend. Toda rota
que exponha custo chama aqui, e é o que faz "esconder o custo dos
usuários finais" ser a mudança de um valor em vez de uma caçada a JSX.

Duas camadas, e a da instância manda:

- `cost_display_enabled` (`config.py`) é o teto do operador. `false`
  esconde de todo mundo, inclusive de quem tem a preferência ligada.
- `user_preferences.show_cost_usd` é por conta, e só pode DESLIGAR o
  que o operador deixou ligado.

Sem o teto, uma conta com a preferência ligada continuaria vendo o
valor depois de o operador decidir que custo não é assunto de usuário
final — exatamente o cenário que a flag existe para cobrir.

**O que isto é e o que não é.** É ocultação de interface, não controle
de acesso: a rota de uso continua devolvendo o número quando o custo
está visível, e quem abrir o DevTools vê o JSON. Se o requisito virar
"o usuário não pode nem descobrir", o lugar certo é a própria rota
omitir o campo — o que custa um SELECT a mais no caminho quente, e por
isso não foi feito antes de ser pedido.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings


async def mostrar_custo(db: AsyncSession, user_id: UUID) -> bool:
    """`True` se esta conta pode ver valores em dólar."""
    if not get_settings().cost_display_enabled:
        return False

    valor = (
        await db.execute(
            text(
                "SELECT show_cost_usd FROM core.user_preferences "
                "WHERE user_id = :uid"
            ),
            {"uid": str(user_id)},
        )
    ).scalar_one_or_none()

    # Conta sem linha em `user_preferences` é caso normal (as
    # preferências nascem no primeiro save), e o default da coluna é
    # `true` — então ausência significa "mostra", igual ao que o banco
    # faria.
    return True if valor is None else bool(valor)
