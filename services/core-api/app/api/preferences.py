"""Preferências do usuário.

ARQUITETURA.md §6.1. A maioria dos campos só passa a ser usada no M2
(agendamento) e M4 (destino), mas a rota existe desde já porque o
onboarding precisa gravar fuso e destino padrão.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from app.core.deps import CurrentUser, DbSession
from app.models import UserPreferences
from app.services.exibicao_de_custo import mostrar_custo

router = APIRouter(prefix="/preferences", tags=["preferences"])


class PreferencesOut(BaseModel):
    working_hours: dict | None
    blocked_windows: dict | None
    # Lista de faixas, não mapa por dia: a preferência de horário é a
    # mesma em qualquer dia. `working_hours` é por dia porque sábado
    # costuma ser diferente de segunda.
    preferred_study_times: list | None
    session_min: int
    session_max: int
    break_min: int
    max_hours_per_day: int
    buffer_min: int
    autonomy_level: str
    spaced_repetition_enabled: bool
    default_target: str
    private_mode: bool
    # Já com o teto da instância aplicado: o front lê isto como "mostro
    # dinheiro?" e não precisa conhecer as duas camadas. Por isso NÃO
    # vem do `model_validate` direto — ver `get_preferences`.
    show_cost_usd: bool


class PreferencesIn(BaseModel):
    working_hours: dict | None = None
    blocked_windows: dict | None = None
    preferred_study_times: list | None = None
    session_min: int | None = Field(default=None, ge=15, le=180)
    session_max: int | None = Field(default=None, ge=15, le=300)
    break_min: int | None = Field(default=None, ge=0, le=60)
    max_hours_per_day: int | None = Field(default=None, ge=1, le=16)
    buffer_min: int | None = Field(default=None, ge=0, le=120)
    autonomy_level: Literal["propose", "auto_create", "auto_all"] | None = None
    spaced_repetition_enabled: bool | None = None
    default_target: Literal["event", "task", "both", "ask"] | None = None
    private_mode: bool | None = None
    show_cost_usd: bool | None = None


async def _load(db: DbSession, user: CurrentUser) -> UserPreferences:
    row = (
        await db.execute(
            select(UserPreferences).where(UserPreferences.user_id == user.id)
        )
    ).scalar_one_or_none()

    if row is None:
        # Conta criada antes desta tabela existir, ou criada por caminho
        # que não passou pelo registro. Cria com os defaults.
        row = UserPreferences(user_id=user.id)
        db.add(row)
        await db.flush()
    return row


async def _saida(db: DbSession, user: CurrentUser, row: UserPreferences):
    """Monta a resposta com o teto da instância já aplicado.

    `show_cost_usd` sai daqui como a resposta FINAL de "mostro
    dinheiro?", e não como o valor cru da coluna: com
    `cost_display_enabled` desligado, uma conta com a preferência
    ligada veria `true` e o front mostraria o custo que o operador
    acabou de esconder.

    Quem decide é `mostrar_custo`, a mesma função que a rota de uso
    chama — uma pergunta, uma resposta, um lugar.
    """
    out = PreferencesOut.model_validate(row, from_attributes=True)
    return out.model_copy(update={"show_cost_usd": await mostrar_custo(db, user.id)})


@router.get("")
async def get_preferences(user: CurrentUser, db: DbSession) -> PreferencesOut:
    row = await _load(db, user)
    return await _saida(db, user, row)


@router.put("")
async def update_preferences(
    body: PreferencesIn, user: CurrentUser, db: DbSession
) -> PreferencesOut:
    await _load(db, user)
    values = body.model_dump(exclude_none=True)
    if values:
        values["updated_at"] = datetime.now(UTC)
        await db.execute(
            update(UserPreferences)
            .where(UserPreferences.user_id == user.id)
            .values(**values)
        )
    row = await _load(db, user)
    return await _saida(db, user, row)
