"""Qual chave de LLM usar neste turno, e se ele cabe na cota.

ARQUITETURA.md §14.2, que previu este modo:

    "a saída é um modo opcional em que o operador fornece a chave com
    cota por usuário — o esquema já suporta (`llm_credentials` por
    usuário não impede uma credencial de instância), mas aí o custo
    passa a ser seu."

**A precedência é a chave da INSTÂNCIA primeiro.** Isso é uma decisão de
produto do operador, não um detalhe: o objetivo é que ninguém precise
criar conta no DeepSeek para mandar a primeira mensagem. Quem colar a
própria chave nas Configurações passa a usá-la — e aí sem cota, porque
o custo é dele.

A ordem, então:

  1. credencial própria do usuário, ativa  ->  usa, SEM cota
  2. chave da instância, se configurada    ->  usa, COM cota
  3. nenhuma das duas                      ->  erro acionável

O passo 1 vem antes do 2 de propósito, apesar de a instância ser a
"prioritária" no sentido de ser o caminho padrão. Não há conflito: quem
não colou chave nenhuma cai no 2, que é o caso da esmagadora maioria.
Quem colou chave fez isso para usá-la, e ignorá-la seria gastar o
dinheiro do operador contra a vontade explícita do usuário — além de
tornar a tela de Configurações uma mentira.

**A cota é medida em custo, não em número de mensagens.** Uma conversa
longa com histórico grande custa ordens de magnitude mais que um "oi", e
um limite por mensagem trataria as duas igual: ou é generoso demais para
conter custo, ou apertado demais para uso normal. O custo estimado já é
gravado por turno em `ai.turn_usage` desde o M1, com índice
`(user_id, created_at DESC)` — a consulta desta cota é exatamente o que
aquele índice serve.

**A cota mora na conta do usuário** (`core.users.monthly_quota_usd`), e
não numa constante do código. É o que torna cobrança uma mudança de
dado em vez de uma mudança de caminho crítico: no dia em que houver
plano pago, confirmar o pagamento é escrever outro número nessa coluna.
Nada aqui muda.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.crypto import SecretBox, llm_context

log = logging.getLogger("ai-worker.credential")


class QuotaExceeded(Exception):
    """A cota do mês acabou e o usuário não tem chave própria.

    Carrega os números para a mensagem poder ser específica: "você usou
    X de Y" é acionável, "cota excedida" não é.
    """

    def __init__(self, used_usd: Decimal, limit_usd: Decimal) -> None:
        self.used_usd = used_usd
        self.limit_usd = limit_usd
        super().__init__(f"cota excedida: {used_usd} de {limit_usd} USD")


class NoCredential(Exception):
    """Nem chave própria nem chave de instância."""


@dataclass(frozen=True)
class ResolvedCredential:
    """A chave a usar, já decifrada, e de onde ela veio.

    `source` existe para o resto do sistema poder se comportar
    diferente sem reinspecionar a decisão: o turno gravado precisa dizer
    de quem foi o custo, e a mensagem de erro de cota só faz sentido
    para `instance`.
    """

    api_key: str
    provider_key: str
    model: str | None
    base_url: str | None
    source: str  # "user" | "instance"

    @property
    def is_instance(self) -> bool:
        return self.source == "instance"


async def month_usage_usd(db: AsyncSession, user_id: UUID) -> Decimal:
    """Custo estimado acumulado no mês corrente, só do que a instância pagou.

    Filtra por `used_instance_key = true`: turnos em que o usuário usou
    a chave dele não custaram nada ao operador e não podem consumir a
    cota. Sem esse filtro, quem colasse a própria chave veria a cota
    cair por uso que ele mesmo pagou.

    `date_trunc('month', now())` e não "últimos 30 dias": a cota é
    mensal no sentido de calendário, que é o que alguém entende ao ler
    "seu limite deste mês" — e é o que vai bater com uma fatura, no dia
    em que houver.
    """
    row = (
        await db.execute(
            text(
                """
                SELECT COALESCE(SUM(estimated_cost_usd), 0)
                FROM ai.turn_usage
                WHERE user_id = :uid
                  AND used_instance_key = true
                  AND created_at >= date_trunc('month', now())
                """
            ),
            {"uid": str(user_id)},
        )
    ).first()
    return Decimal(str(row[0])) if row and row[0] is not None else Decimal(0)


async def resolve(
    db: AsyncSession,
    user_id: UUID,
    conversation_id: UUID,
) -> ResolvedCredential:
    """Decide a credencial do turno. Levanta se não houver caminho.

    A consulta de credencial do usuário é a mesma de antes, inclusive o
    `COALESCE` que respeita o provedor escolhido na conversa — só o que
    acontece quando ela não encontra nada é novo.
    """
    settings = get_settings()

    cred = (
        await db.execute(
            text(
                """
                SELECT provider_key, encrypted_api_key, default_model, base_url
                FROM core.llm_credentials
                WHERE user_id = :uid AND is_active = true
                  AND provider_key = COALESCE(
                      (SELECT provider_key FROM core.conversations
                       WHERE id = :cid),
                      provider_key
                  )
                LIMIT 1
                """
            ),
            {"uid": str(user_id), "cid": str(conversation_id)},
        )
    ).first()

    if cred is not None:
        provider_key, blob, default_model, base_url = cred
        return ResolvedCredential(
            api_key=SecretBox(settings.master_key).decrypt(
                bytes(blob), llm_context(user_id)
            ),
            provider_key=provider_key,
            model=default_model,
            base_url=base_url,
            source="user",
        )

    if not settings.has_instance_key:
        raise NoCredential()

    # A partir daqui é a chave do operador, e o custo é dele — então a
    # cota vale.
    limite = await _quota_limit(db, user_id)
    if limite > 0:
        usado = await month_usage_usd(db, user_id)
        if usado >= limite:
            log.info(
                "cota do mês esgotada user=%s usado=%s limite=%s",
                user_id,
                usado,
                limite,
            )
            raise QuotaExceeded(used_usd=usado, limit_usd=limite)

    return ResolvedCredential(
        api_key=settings.instance_llm_api_key.strip(),
        provider_key=settings.instance_llm_provider,
        # String vazia na variável de ambiente significa "o default do
        # adaptador", e `build_llm` já trata `None` assim. Converter aqui
        # evita passar "" adiante e o provedor pedir um modelo chamado "".
        model=settings.instance_llm_model.strip() or None,
        base_url=settings.instance_llm_base_url.strip() or None,
        source="instance",
    )


async def _quota_limit(db: AsyncSession, user_id: UUID) -> Decimal:
    """O limite desta conta, em USD. 0 ou NULL = ilimitado.

    Ilimitado é representado por 0 e não por um número grande, porque
    "sem limite" é uma intenção e não um valor alto — é assim que
    liberar uma conta específica (a sua, por exemplo) não depende de
    adivinhar um teto que nunca será atingido.
    """
    row = (
        await db.execute(
            text("SELECT monthly_quota_usd FROM core.users WHERE id = :uid"),
            {"uid": str(user_id)},
        )
    ).first()
    if row is None or row[0] is None:
        return Decimal(0)
    return Decimal(str(row[0]))
