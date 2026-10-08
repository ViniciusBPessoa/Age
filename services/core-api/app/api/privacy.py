"""Exportação, exclusão e os limites honestos.

ARQUITETURA.md §6.2.5 e §9.6. Esta é a tela em que o produto tem de
dizer a verdade sobre o que NÃO consegue fazer, e as frases estão no
código porque são conteúdo, não decoração:

- **O que já foi enviado ao provedor de LLM não volta.** Apagar aqui
  apaga do Age; o que o DeepSeek ou o OpenAI retiveram segue a política
  deles. Prometer o contrário seria mentira.
- **Backup tem retenção própria.** A conversa sai do sistema ativo na
  hora, mas pode persistir em backup cifrado até o ciclo expirar.
- **Eventos criados em provedor externo não são apagados.** São do
  usuário, estão na agenda dele, e apagá-los ao excluir a conta seria
  destrutivo.

Omitir qualquer uma delas vira quebra de confiança quando o usuário
descobre sozinho — que é pior que o limite em si.

Três níveis de exclusão, com escopos distintos (§9.6):

| Ação | Apaga | Preserva |
|---|---|---|
| Apagar conversa | o diálogo daquela conversa | eventos, tarefas, planos |
| Apagar histórico | todos os diálogos | tudo que foi aplicado |
| Apagar conta | tudo no Age | eventos em provedor externo |

Janela de 30 dias para desfazer nos três.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.deps import CurrentUser, DbSession

log = logging.getLogger("core-api.privacy")
router = APIRouter(prefix="/privacy", tags=["privacy"])

UNDO_WINDOW = timedelta(days=30)

# Os três avisos de §6.2.5 e §9.6, num lugar só. A UI os mostra na
# tela; o texto vive aqui para não divergir entre a tela e a resposta
# da API — e para que mudá-lo seja uma decisão, não um descuido.
LIMIT_LLM = (
    "O que já foi enviado ao provedor de LLM não volta. Apagar aqui "
    "apaga do Age; o que o DeepSeek, o OpenAI ou o Gemini retiveram "
    "segue a política deles."
)
LIMIT_BACKUP = (
    "Backup do banco tem retenção própria. A conversa sai do sistema "
    "ativo imediatamente, mas pode persistir em backup cifrado até o "
    "ciclo de retenção expirar."
)
LIMIT_EXTERNAL_EVENTS = (
    "Eventos que o Age criou no seu calendário externo não são "
    "apagados. Eles são seus, estão na sua agenda, e removê-los seria "
    "destrutivo — se quiser tirar, dá para fazer antes, pela lista "
    "abaixo."
)


class LimitsOut(BaseModel):
    llm: str
    backup: str
    external_events: str
    undo_window_days: int


class ExportCounts(BaseModel):
    conversations: int
    messages: int
    events: int
    tasks: int
    routines: int
    study_subjects: int
    plans: int


class DeletionPreviewOut(BaseModel):
    """O que vai sair e o que vai ficar, antes de confirmar.

    Mostrar isto antes é o que diferencia uma exclusão informada de um
    clique em "apagar tudo" seguido de arrependimento. Os números são
    reais, não estimados.
    """

    scope: str
    will_delete: ExportCounts
    will_preserve: list[str]
    limits: LimitsOut
    undo_until: datetime
    external_events_created: int


@router.get("/limits", response_model=LimitsOut)
async def limits(user: CurrentUser) -> LimitsOut:
    """Os limites honestos, para a tela mostrar sem repetir texto."""
    return LimitsOut(
        llm=LIMIT_LLM,
        backup=LIMIT_BACKUP,
        external_events=LIMIT_EXTERNAL_EVENTS,
        undo_window_days=UNDO_WINDOW.days,
    )


async def _counts(db, user_id) -> ExportCounts:
    row = (
        await db.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM core.conversations
                     WHERE user_id = :uid AND deleted_at IS NULL),
                  (SELECT count(*) FROM core.messages m
                     JOIN core.conversations c ON c.id = m.conversation_id
                     WHERE c.user_id = :uid AND m.deleted_at IS NULL),
                  (SELECT count(*) FROM core.local_events
                     WHERE user_id = :uid AND deleted_at IS NULL),
                  (SELECT count(*) FROM core.local_tasks
                     WHERE user_id = :uid AND deleted_at IS NULL),
                  (SELECT count(*) FROM core.routines
                     WHERE user_id = :uid AND ended_at IS NULL),
                  (SELECT count(*) FROM core.study_subjects
                     WHERE user_id = :uid),
                  (SELECT count(*) FROM core.plans WHERE user_id = :uid)
                """
            ),
            {"uid": str(user_id)},
        )
    ).first()
    assert row is not None
    return ExportCounts(
        conversations=row[0],
        messages=row[1],
        events=row[2],
        tasks=row[3],
        routines=row[4],
        study_subjects=row[5],
        plans=row[6],
    )


@router.get("/deletion-preview", response_model=DeletionPreviewOut)
async def deletion_preview(
    user: CurrentUser,
    db: DbSession,
    scope: Annotated[
        str, Query(pattern="^(history|account)$")
    ] = "history",
) -> DeletionPreviewOut:
    counts = await _counts(db, user.id)

    external = (
        await db.execute(
            text(
                """
                SELECT count(*) FROM core.events_shadow
                WHERE user_id = :uid AND created_by_age
                  AND deleted_at IS NULL
                """
            ),
            {"uid": str(user.id)},
        )
    ).scalar_one()

    if scope == "history":
        will_delete = ExportCounts(
            conversations=counts.conversations,
            messages=counts.messages,
            events=0,
            tasks=0,
            routines=0,
            study_subjects=0,
            plans=0,
        )
        preserve = [
            f"{counts.events} eventos na sua agenda",
            f"{counts.tasks} tarefas",
            f"{counts.routines} rotinas",
            f"{counts.study_subjects} assuntos de estudo",
            "tudo que foi aplicado a partir de um plano",
        ]
    else:
        will_delete = counts
        preserve = []
        if external:
            preserve.append(
                f"{external} eventos que o Age criou no seu calendário "
                "externo (veja o aviso abaixo)"
            )

    return DeletionPreviewOut(
        scope=scope,
        will_delete=will_delete,
        will_preserve=preserve,
        limits=LimitsOut(
            llm=LIMIT_LLM,
            backup=LIMIT_BACKUP,
            external_events=LIMIT_EXTERNAL_EVENTS,
            undo_window_days=UNDO_WINDOW.days,
        ),
        undo_until=datetime.now(UTC) + UNDO_WINDOW,
        external_events_created=external,
    )


@router.get("/export")
async def export_data(user: CurrentUser, db: DbSession) -> dict:
    """Exportação completa em JSON (§9.6).

    Inclui as conversas. Nenhum dado é usado para treinar nada, e o
    export existe para que isso seja verificável — não só afirmado.
    """
    out: dict = {
        "exported_at": datetime.now(UTC).isoformat(),
        "account": {},
        "conversations": [],
        "events": [],
        "tasks": [],
        "routines": [],
        "study": [],
        "limits": {
            "llm": LIMIT_LLM,
            "backup": LIMIT_BACKUP,
        },
    }

    account = (
        await db.execute(
            text(
                "SELECT email, name, timezone, locale, created_at "
                "FROM core.users WHERE id = :uid"
            ),
            {"uid": str(user.id)},
        )
    ).first()
    if account:
        out["account"] = {
            "email": account[0],
            "name": account[1],
            "timezone": account[2],
            "locale": account[3],
            "created_at": account[4].isoformat(),
        }

    conversations = await db.execute(
        text(
            """
            SELECT c.id, c.title, c.created_at,
                   COALESCE(json_agg(
                     json_build_object(
                       'role', m.role,
                       'content', m.content,
                       'created_at', m.created_at
                     ) ORDER BY m.created_at
                   ) FILTER (WHERE m.id IS NOT NULL), '[]'::json)
            FROM core.conversations c
            LEFT JOIN core.messages m
                   ON m.conversation_id = c.id AND m.deleted_at IS NULL
            WHERE c.user_id = :uid AND c.deleted_at IS NULL
            GROUP BY c.id, c.title, c.created_at
            ORDER BY c.created_at
            """
        ),
        {"uid": str(user.id)},
    )
    out["conversations"] = [
        {
            "id": str(r[0]),
            "title": r[1],
            "created_at": r[2].isoformat(),
            "messages": r[3],
        }
        for r in conversations.all()
    ]

    events = await db.execute(
        text(
            """
            SELECT title, start_at, end_at, flexibility, created_by_age
            FROM core.local_events
            WHERE user_id = :uid AND deleted_at IS NULL
            ORDER BY start_at
            """
        ),
        {"uid": str(user.id)},
    )
    out["events"] = [
        {
            "title": r[0],
            "start": r[1].isoformat(),
            "end": r[2].isoformat(),
            "flexibility": r[3],
            "created_by_age": r[4],
        }
        for r in events.all()
    ]

    tasks = await db.execute(
        text(
            """
            SELECT title, notes, due_date, due_at, status, completed_at,
                   priority, estimated_min
            FROM core.local_tasks
            WHERE user_id = :uid AND deleted_at IS NULL
            ORDER BY created_at
            """
        ),
        {"uid": str(user.id)},
    )
    out["tasks"] = [
        {
            "title": r[0],
            "notes": r[1],
            "due_date": r[2].isoformat() if r[2] else None,
            "due_at": r[3].isoformat() if r[3] else None,
            "status": r[4],
            "completed_at": r[5].isoformat() if r[5] else None,
            "priority": r[6],
            "estimated_min": r[7],
        }
        for r in tasks.all()
    ]

    routines = await db.execute(
        text(
            """
            SELECT r.title, r.rrule, r.duration_min, r.target_per_week,
                   r.started_at, r.paused_at, r.ended_at,
                   COALESCE(json_agg(
                     json_build_object(
                       'scheduled_start', o.scheduled_start,
                       'status', o.status,
                       'actual_min', o.actual_min
                     ) ORDER BY o.scheduled_start
                   ) FILTER (WHERE o.id IS NOT NULL), '[]'::json)
            FROM core.routines r
            LEFT JOIN core.routine_occurrences o ON o.routine_id = r.id
            WHERE r.user_id = :uid
            GROUP BY r.id, r.title, r.rrule, r.duration_min,
                     r.target_per_week, r.started_at, r.paused_at,
                     r.ended_at
            ORDER BY r.created_at
            """
        ),
        {"uid": str(user.id)},
    )
    out["routines"] = [
        {
            "title": r[0],
            "rrule": r[1],
            "duration_min": r[2],
            "target_per_week": r[3],
            "started_at": r[4].isoformat(),
            "paused_at": r[5].isoformat() if r[5] else None,
            "ended_at": r[6].isoformat() if r[6] else None,
            "occurrences": r[7],
        }
        for r in routines.all()
    ]

    study = await db.execute(
        text(
            """
            SELECT s.name, s.deadline, s.status,
                   COALESCE(json_agg(
                     json_build_object(
                       'name', t.name,
                       'estimated_min', t.estimated_min,
                       'status', t.status
                     ) ORDER BY t.order_index
                   ) FILTER (WHERE t.id IS NOT NULL), '[]'::json)
            FROM core.study_subjects s
            LEFT JOIN core.study_topics t ON t.subject_id = s.id
            WHERE s.user_id = :uid
            GROUP BY s.id, s.name, s.deadline, s.status
            ORDER BY s.created_at
            """
        ),
        {"uid": str(user.id)},
    )
    out["study"] = [
        {
            "name": r[0],
            "deadline": r[1].isoformat() if r[1] else None,
            "status": r[2],
            "topics": r[3],
        }
        for r in study.all()
    ]

    return out


class DeleteHistoryOut(BaseModel):
    conversations_deleted: int
    undo_until: datetime
    preserved: list[str]
    limits: LimitsOut
    message: str


@router.post("/delete-history", response_model=DeleteHistoryOut)
async def delete_history(user: CurrentUser, db: DbSession) -> DeleteHistoryOut:
    """Apaga todos os diálogos, preserva o que foi aplicado (§6.2.5).

    Soft delete, com a janela de 30 dias. O job de expurgo faz o resto
    — e é ele que torna a janela real em vez de promessa.
    """
    result = await db.execute(
        text(
            """
            UPDATE core.conversations
            SET deleted_at = now()
            WHERE user_id = :uid AND deleted_at IS NULL
            """
        ),
        {"uid": str(user.id)},
    )
    count = result.rowcount or 0

    counts = await _counts(db, user.id)
    return DeleteHistoryOut(
        conversations_deleted=count,
        undo_until=datetime.now(UTC) + UNDO_WINDOW,
        preserved=[
            f"{counts.events} eventos na sua agenda",
            f"{counts.tasks} tarefas",
            f"{counts.routines} rotinas",
        ],
        limits=LimitsOut(
            llm=LIMIT_LLM,
            backup=LIMIT_BACKUP,
            external_events=LIMIT_EXTERNAL_EVENTS,
            undo_window_days=UNDO_WINDOW.days,
        ),
        message=(
            f"{count} conversas apagadas. Sua agenda, tarefas e rotinas "
            f"continuam como estavam. Dá para desfazer por "
            f"{UNDO_WINDOW.days} dias."
        ),
    )


class DeleteAccountIn(BaseModel):
    """Confirmação por digitação, não por checkbox.

    Apagar a conta é a ação mais destrutiva do produto, e um clique
    acidental não deve bastar. Digitar o próprio e-mail é o padrão que
    o usuário já conhece de outros serviços.
    """

    confirm_email: str = Field(min_length=3)
    understood_external_events: bool = False


class DeleteAccountOut(BaseModel):
    scheduled: bool
    purge_at: datetime
    external_events_kept: int
    limits: LimitsOut
    message: str


@router.post("/delete-account", response_model=DeleteAccountOut)
async def delete_account(
    payload: DeleteAccountIn, user: CurrentUser, db: DbSession
) -> DeleteAccountOut:
    """Agenda a exclusão da conta, com janela de 30 dias (§9.6).

    Não apaga na hora, de propósito: a janela de desfazer é o que
    transforma um arrependimento em um clique, e não em um ticket de
    suporte que não tem solução.
    """
    email = (
        await db.execute(
            text("SELECT email FROM core.users WHERE id = :uid"),
            {"uid": str(user.id)},
        )
    ).scalar_one_or_none()

    if not email or payload.confirm_email.strip().lower() != str(
        email
    ).strip().lower():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Digite o e-mail da conta para confirmar. Isso existe "
                "para um clique acidental não apagar tudo."
            ),
        )

    external = (
        await db.execute(
            text(
                """
                SELECT count(*) FROM core.events_shadow
                WHERE user_id = :uid AND created_by_age
                  AND deleted_at IS NULL
                """
            ),
            {"uid": str(user.id)},
        )
    ).scalar_one()

    if external and not payload.understood_external_events:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "external_events_need_acknowledgement",
                    "message": LIMIT_EXTERNAL_EVENTS,
                    "details": {"external_events_created": external},
                }
            },
        )

    await db.execute(
        text(
            "UPDATE core.users SET deleted_at = now() WHERE id = :uid"
        ),
        {"uid": str(user.id)},
    )
    # Revoga as sessões na hora: a conta está encerrada do ponto de
    # vista do usuário, mesmo que o expurgo só venha em 30 dias.
    await db.execute(
        text(
            """
            UPDATE core.sessions
            SET revoked_at = now(), revoked_reason = 'account_deleted'
            WHERE user_id = :uid AND revoked_at IS NULL
            """
        ),
        {"uid": str(user.id)},
    )

    log.info("conta %s marcada para exclusão", user.id)

    purge_at = datetime.now(UTC) + UNDO_WINDOW
    return DeleteAccountOut(
        scheduled=True,
        purge_at=purge_at,
        external_events_kept=external,
        limits=LimitsOut(
            llm=LIMIT_LLM,
            backup=LIMIT_BACKUP,
            external_events=LIMIT_EXTERNAL_EVENTS,
            undo_window_days=UNDO_WINDOW.days,
        ),
        message=(
            f"Conta encerrada. O expurgo definitivo acontece em "
            f"{purge_at:%d/%m/%Y}; até lá, entrar de novo desfaz. "
            + (
                f"Os {external} eventos que eu criei no seu calendário "
                "externo continuam lá."
                if external
                else ""
            )
        ),
    )
