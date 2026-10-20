"""Consumo de tokens e custo, por período (§12).

A medição existe desde o M1 — `ai.turn_usage` guarda tokens, custo,
modelo e turno a cada resposta —, mas nada a lia. `core.usage_log`, que
um comentário chamava de "o que a tela de custo lê", era write-only: a
tela não existia.

**A fonte é `ai.turn_usage`, não `core.usage_log`.** As duas guardam o
mesmo uso, mas a primeira tem `turn_id` e `used_instance_key` e a
segunda não — ela é uma cópia estritamente mais pobre, feita para que o
schema do core tenha a trilha de auditoria. O core-api já lê
`ai.turn_usage` em `llm.py` (a soma da cota), pelo GRANT de leitura do
schema `ai`, então agregar a partir da fonte rica não custa nada e livra
o painel de depender de o dreno preguiçoso ter rodado.

Duas decisões de SQL decidem se o gráfico mente:

1. **`date_trunc` no fuso da conta.** Uso às 21h em São Paulo pertence
   àquele dia; truncar em UTC o empurraria para o seguinte, e a série
   inteira ficaria deslocada.
2. **`generate_series` para os dias sem uso.** Sem isso o gráfico
   comprime os vãos, e um fim de semana parado parece não ter
   existido — sete barras seguidas mentiriam sobre o ritmo da semana.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import text

from app.core.deps import CurrentUser, DbSession
from app.services.calendar_access import load_policy
from app.services.exibicao_de_custo import mostrar_custo

router = APIRouter(prefix="/usage", tags=["usage"])


# `date_trunc` e `interval` recebem o passo como texto. O valor vem de
# um `Literal` do Pydantic, mas ele não entra na query direto: entra por
# este dicionário. Um `Literal` validado é defesa em outro arquivo, e
# basta alguém relaxar o tipo "para aceitar trimestre" para abrir
# injeção. Constante local não tem esse caminho.
_PASSOS = {
    "day": ("day", "1 day"),
    "week": ("week", "1 week"),
    "month": ("month", "1 month"),
}

# Teto de pontos devolvidos. A tela amarra o passo ao período, mas a
# rota é pública: sem isto, `?bucket=day` em dez anos devolve 3650
# linhas e um gráfico ilegível.
_MAX_PONTOS = 400


class UsageBucketOut(BaseModel):
    """Um ponto da série temporal, já no fuso da conta."""

    day: str
    prompt_tokens: int
    completion_tokens: int
    cached_prompt_tokens: int | None
    # `None` quando nenhum uso do período tinha preço conhecido, ou
    # quando não houve uso. Nunca `0.0`, que afirmaria gratuidade.
    estimated_cost_usd: float | None


class UsageByModelOut(BaseModel):
    provider_key: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cached_prompt_tokens: int | None
    estimated_cost_usd: float | None
    turns: int


class UsageSummaryOut(BaseModel):
    period_start: datetime
    period_end: datetime
    bucket: Literal["day", "week", "month"]
    timezone: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cached_prompt_tokens: int | None
    estimated_cost_usd: float | None
    turns: int
    # Decidido no servidor para que desligar seja um UPDATE, não um
    # deploy do front.
    show_cost: bool


class UsageReportOut(UsageSummaryOut):
    buckets: list[UsageBucketOut]
    by_model: list[UsageByModelOut]


def _opcional(valor: object) -> float | None:
    """`SUM` de coluna toda nula devolve `NULL`, e isso é informação."""
    return None if valor is None else float(valor)


def _inteiro_opcional(valor: object) -> int | None:
    return None if valor is None else int(valor)


@router.get("/summary", response_model=UsageReportOut)
async def usage_summary(
    user: CurrentUser,
    db: DbSession,
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
    bucket: Annotated[Literal["day", "week", "month"], Query()] = "day",
) -> UsageReportOut:
    """Tokens e custo do período, por dia e por modelo.

    Sem período, os últimos 30 dias — a janela que responde "quanto eu
    gastei este mês?" sem o usuário precisar escolher nada.
    """
    agora = datetime.now(UTC)
    fim = end or agora
    inicio = start or (fim - timedelta(days=30))

    if inicio >= fim:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="O início do período tem de vir antes do fim.",
        )

    passo_sql, intervalo = _PASSOS[bucket]
    dias = (fim - inicio).days + 1
    pontos = dias if bucket == "day" else dias // (7 if bucket == "week" else 28)
    if pontos > _MAX_PONTOS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Período longo demais para o passo '{bucket}': daria "
                f"{pontos} pontos. Use um passo maior."
            ),
        )

    policy = await load_policy(db, user.id)
    tz = policy.timezone

    params = {
        "uid": str(user.id),
        "inicio": inicio,
        "fim": fim,
        "tz": tz,
    }

    # Série temporal. `AT TIME ZONE :tz` converte o instante para a hora
    # local da conta ANTES de truncar, que é o que põe o uso das 21h no
    # dia certo. O LEFT JOIN com `generate_series` devolve os dias sem
    # uso, que a barra desenha como vão e não como ausência.
    serie = (
        await db.execute(
            text(
                f"""
                WITH dias AS (
                    SELECT generate_series(
                        date_trunc(
                            '{passo_sql}', (:inicio)::timestamptz AT TIME ZONE :tz
                        ),
                        date_trunc(
                            '{passo_sql}', (:fim)::timestamptz AT TIME ZONE :tz
                        ),
                        '{intervalo}'::interval
                    ) AS dia
                ),
                uso AS (
                    SELECT date_trunc(
                               '{passo_sql}', created_at AT TIME ZONE :tz
                           ) AS dia,
                           SUM(prompt_tokens)::bigint AS pt,
                           SUM(completion_tokens)::bigint AS ct,
                           SUM(cached_prompt_tokens)::bigint AS cached,
                           SUM(estimated_cost_usd) AS custo
                    FROM ai.turn_usage
                    WHERE user_id = :uid
                      AND created_at >= :inicio
                      AND created_at < :fim
                    GROUP BY 1
                )
                SELECT to_char(d.dia, 'YYYY-MM-DD'),
                       COALESCE(u.pt, 0), COALESCE(u.ct, 0),
                       u.cached, u.custo
                FROM dias d
                LEFT JOIN uso u ON u.dia = d.dia
                ORDER BY d.dia
                """
            ),
            params,
        )
    ).all()

    por_modelo = (
        await db.execute(
            text(
                """
                SELECT provider_key, model,
                       SUM(prompt_tokens)::bigint,
                       SUM(completion_tokens)::bigint,
                       SUM(cached_prompt_tokens)::bigint,
                       SUM(estimated_cost_usd),
                       COUNT(*)::bigint
                FROM ai.turn_usage
                WHERE user_id = :uid
                  AND created_at >= :inicio
                  AND created_at < :fim
                GROUP BY provider_key, model
                ORDER BY SUM(prompt_tokens) DESC
                """
            ),
            params,
        )
    ).all()

    totais = (
        await db.execute(
            text(
                """
                SELECT COALESCE(SUM(prompt_tokens), 0)::bigint,
                       COALESCE(SUM(completion_tokens), 0)::bigint,
                       SUM(cached_prompt_tokens)::bigint,
                       SUM(estimated_cost_usd),
                       COUNT(*)::bigint
                FROM ai.turn_usage
                WHERE user_id = :uid
                  AND created_at >= :inicio
                  AND created_at < :fim
                """
            ),
            params,
        )
    ).first()

    pt, ct, cached, custo, turnos = totais if totais else (0, 0, None, None, 0)

    return UsageReportOut(
        period_start=inicio,
        period_end=fim,
        bucket=bucket,
        timezone=tz,
        prompt_tokens=int(pt),
        completion_tokens=int(ct),
        total_tokens=int(pt) + int(ct),
        cached_prompt_tokens=_inteiro_opcional(cached),
        estimated_cost_usd=_opcional(custo),
        turns=int(turnos),
        show_cost=await mostrar_custo(db, user.id),
        buckets=[
            UsageBucketOut(
                day=linha[0],
                prompt_tokens=int(linha[1]),
                completion_tokens=int(linha[2]),
                cached_prompt_tokens=_inteiro_opcional(linha[3]),
                estimated_cost_usd=_opcional(linha[4]),
            )
            for linha in serie
        ],
        by_model=[
            UsageByModelOut(
                provider_key=linha[0],
                model=linha[1],
                prompt_tokens=int(linha[2]),
                completion_tokens=int(linha[3]),
                cached_prompt_tokens=_inteiro_opcional(linha[4]),
                estimated_cost_usd=_opcional(linha[5]),
                turns=int(linha[6]),
            )
            for linha in por_modelo
        ],
    )
