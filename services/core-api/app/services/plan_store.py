"""Persistência e promoção de planos.

ARQUITETURA.md §5.7 e §2.3.2. Duas responsabilidades:

1. **Promover rascunhos** de `ai.plan_drafts` para `core.plans`,
   resolvendo os eventos referenciados e validando conflitos. A
   validação roda aqui, na leitura — não na proposta — então um
   conflito que surgiu nesse intervalo aparece.

2. **Carregar e marcar** planos para as rotas de aprovação.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.plan_diff import (
    DEFAULT_TTL,
    Conflict,
    ConflictKind,
    DiffItem,
    ItemStatus,
    Op,
    Plan,
    PlanStatus,
    Target,
    validate_plan,
)
from app.ports.calendar import Event, Flexibility
from app.services.calendar_access import load_policy, read_busy, read_events

log = logging.getLogger("core-api.plan_store")


# ---------------------------------------------------------------
# Promoção de rascunho
# ---------------------------------------------------------------
async def promote_drafts(db: AsyncSession, user_id: UUID) -> list[UUID]:
    """Converte rascunhos do worker em planos validados.

    Idempotente: rascunho já promovido é ignorado.
    """
    rows = await db.execute(
        text(
            """
            SELECT id, turn_id, conversation_id, summary, items
            FROM ai.plan_drafts
            WHERE user_id = :uid AND promoted = false
            ORDER BY created_at
            """
        ),
        {"uid": str(user_id)},
    )
    drafts = rows.all()
    if not drafts:
        return []

    created: list[UUID] = []
    for draft_id, _turn_id, conversation_id, summary, items in drafts:
        try:
            plan_id = await _promote_one(
                db, user_id, conversation_id, summary, items or []
            )
        except Exception as exc:  # noqa: BLE001
            # Rascunho ruim não deve impedir os outros nem travar a
            # leitura da conversa. Marca como promovido para não ficar
            # tentando eternamente.
            log.warning("rascunho %s não pôde ser promovido: %s", draft_id, exc)
            await db.execute(
                text(
                    "UPDATE ai.plan_drafts SET promoted = true "
                    "WHERE id = :did AND user_id = :uid"
                ),
                {"did": str(draft_id), "uid": str(user_id)},
            )
            continue

        await db.execute(
            text(
                """
                UPDATE ai.plan_drafts
                SET promoted = true, promoted_plan_id = :pid
                WHERE id = :did AND user_id = :uid
                """
            ),
            {"did": str(draft_id), "pid": str(plan_id), "uid": str(user_id)},
        )
        created.append(plan_id)

    return created


async def _promote_one(
    db: AsyncSession,
    user_id: UUID,
    conversation_id: UUID,
    summary: str | None,
    raw_items: list[dict],
) -> UUID:
    policy = await load_policy(db, user_id)

    # Calendario de escrita, resolvido AQUI e nao no executor: o
    # executor recebe o provedor por porta (secao 3.2) e nao deve saber
    # o conceito de "calendario default". Resolver aqui tambem deixa o
    # item do plano completo quando o usuario o revisa.
    write_calendar_id = await _resolve_write_calendar(db, user_id)
    items: list[DiffItem] = []

    # Janela que cobre todos os itens, para carregar o contexto de
    # validação de uma vez.
    moments = []
    for raw in raw_items:
        payload = raw.get("payload", {})
        for key in ("start", "new_start"):
            if payload.get(key):
                try:
                    moments.append(datetime.fromisoformat(payload[key]))
                except ValueError:
                    pass

    now = datetime.now(UTC)
    # A janela cobre os EVENTOS DO PLANO, não de hoje até eles. Incluir
    # `now` no mínimo parecia defensivo e quebrava qualquer plano para
    # daqui a mais de 90 dias: a janela virava "hoje até o evento" e
    # estourava o limite de §7.0.
    if moments:
        window_start = min(moments) - timedelta(days=1)
        window_end = max(moments) + timedelta(days=1)
    else:
        # Sem horário em nenhum item: janela curta em torno de agora.
        window_start = now - timedelta(days=1)
        window_end = now + timedelta(days=7)

    existing = await read_events(db, user_id, window_start, window_end)
    by_id = {e.id: e for e in existing if e.id}
    busy = await read_busy(db, user_id, window_start, window_end)

    for index, raw in enumerate(raw_items):
        op = raw.get("op", "")
        reason = (raw.get("reason") or "").strip()
        payload = raw.get("payload", {})

        if not reason:
            # `reason` é obrigatório por contrato (§5.6). Rascunho sem
            # ele é descartado em vez de ganhar motivo inventado.
            log.info("item %s descartado: sem reason", index)
            continue

        try:
            item = _build_item(
                op=op,
                reason=reason,
                payload=payload,
                sequence=index,
                existing=by_id,
                timezone=policy.timezone,
                write_calendar_id=write_calendar_id,
            )
        except (ValueError, KeyError) as exc:
            log.info("item %s descartado: %s", index, exc)
            continue

        if item is not None:
            items.append(item)

    plan = Plan(
        id=uuid4(),
        user_id=user_id,
        conversation_id=conversation_id,
        items=items,
        summary=summary or "",
        expires_at=now + DEFAULT_TTL,
    )
    plan = validate_plan(
        plan,
        busy,
        [e for e in existing if e.flexibility == Flexibility.FIXED],
        now=now,
        max_hours_per_day=policy.max_hours_per_day,
    )

    await _persist(db, plan)
    return plan.id


async def _resolve_write_calendar(db: AsyncSession, user_id: UUID) -> str:
    """Onde o Age escreve por padrao.

    Respeita `default_write_calendar_id` das preferencias (modo hibrido,
    secao 3.2.1); senao usa o calendario nativo, criando-o se preciso.
    """
    from app.adapters.calendar.local import LocalCalendar

    preferred = (
        await db.execute(
            text(
                "SELECT default_write_calendar_id "
                "FROM core.user_preferences WHERE user_id = :uid"
            ),
            {"uid": str(user_id)},
        )
    ).scalar_one_or_none()

    if preferred:
        return preferred

    local = LocalCalendar(db=db, user_id=user_id)
    default = await local.ensure_default_calendar()
    return default.id


def _build_item(
    *,
    op: str,
    reason: str,
    payload: dict,
    sequence: int,
    existing: dict[str, Event],
    timezone: str,
    write_calendar_id: str,
) -> DiffItem | None:
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(timezone)

    def parse(value: str) -> datetime:
        moment = datetime.fromisoformat(value)
        # Modelo às vezes manda sem fuso; assume o do usuário em vez de
        # recusar, porque recusar perderia a proposta inteira.
        return moment if moment.tzinfo else moment.replace(tzinfo=tz)

    match op:
        case "CREATE":
            start = parse(payload["start"])
            if payload.get("duration_min"):
                end = start + timedelta(minutes=int(payload["duration_min"]))
            else:
                end = parse(payload["end"])

            return DiffItem(
                id=uuid4(),
                target=Target.EVENT,
                op=Op.CREATE,
                reason=reason,
                sequence=sequence,
                after=Event(
                    id=None,
                    calendar_id=write_calendar_id,
                    title=payload["title"],
                    start=start,
                    end=end,
                    description=payload.get("description"),
                    recurrence=payload.get("rrule"),
                    flexibility=Flexibility(
                        payload.get("flexibility", "flexible")
                    ),
                    source="age",
                ),
            )

        case "MOVE":
            before = existing.get(payload["event_id"])
            if before is None:
                # O modelo pode alucinar um evento. Descartar é melhor
                # que propor mudança em algo que não existe (§9.5).
                raise ValueError(
                    f"evento {payload['event_id']} não existe na agenda"
                )

            from dataclasses import replace

            return DiffItem(
                id=uuid4(),
                target=Target.EVENT,
                op=Op.MOVE,
                reason=reason,
                sequence=sequence,
                target_ref=before.id,
                before=before,
                after=replace(
                    before,
                    start=parse(payload["new_start"]),
                    end=parse(payload["new_end"]),
                ),
            )

        case "DELETE":
            before = existing.get(payload["event_id"])
            if before is None:
                raise ValueError(
                    f"evento {payload['event_id']} não existe na agenda"
                )
            return DiffItem(
                id=uuid4(),
                target=Target.EVENT,
                op=Op.DELETE,
                reason=reason,
                sequence=sequence,
                target_ref=before.id,
                before=before,
            )

        case _:
            raise ValueError(f"operação não suportada: {op}")


# ---------------------------------------------------------------
# Persistência
# ---------------------------------------------------------------
def _event_json(event: Event | None) -> dict | None:
    if event is None:
        return None
    return {
        "id": event.id,
        "calendar_id": event.calendar_id,
        "title": event.title,
        "start": event.start.isoformat(),
        "end": event.end.isoformat(),
        "all_day": event.all_day,
        "description": event.description,
        "location": event.location,
        "recurrence": event.recurrence,
        "flexibility": event.flexibility.value,
        "etag": event.etag,
        "source": event.source,
    }


def _event_from_json(raw: dict | None) -> Event | None:
    if not raw:
        return None
    return Event(
        id=raw.get("id"),
        calendar_id=raw.get("calendar_id") or "",
        title=raw.get("title") or "(sem título)",
        start=datetime.fromisoformat(raw["start"]),
        end=datetime.fromisoformat(raw["end"]),
        all_day=raw.get("all_day", False),
        description=raw.get("description"),
        location=raw.get("location"),
        recurrence=raw.get("recurrence"),
        flexibility=Flexibility(raw.get("flexibility", "fixed")),
        etag=raw.get("etag"),
        source=raw.get("source"),
    )


async def _persist(db: AsyncSession, plan: Plan) -> None:
    await db.execute(
        text(
            """
            INSERT INTO core.plans
              (id, user_id, conversation_id, status, summary,
               created_at, expires_at)
            VALUES (:id, :uid, :cid, :status, :summary, :created, :expires)
            """
        ),
        {
            "id": str(plan.id),
            "uid": str(plan.user_id),
            "cid": str(plan.conversation_id) if plan.conversation_id else None,
            "status": plan.status.value,
            "summary": plan.summary,
            "created": plan.created_at,
            "expires": plan.expires_at,
        },
    )

    for item in plan.items:
        await db.execute(
            text(
                """
                INSERT INTO core.plan_items
                  (id, user_id, plan_id, target, op, target_ref,
                   before, after, reason, confidence, conflicts, links,
                   sequence, status)
                VALUES (:id, :uid, :pid, :target, :op, :ref,
                        CAST(:before AS json), CAST(:after AS json),
                        :reason, :conf, CAST(:conflicts AS json),
                        CAST(:links AS json), :seq, :status)
                """
            ),
            {
                "id": str(item.id),
                "uid": str(plan.user_id),
                "pid": str(plan.id),
                "target": item.target.value,
                "op": item.op.value,
                "ref": item.target_ref,
                "before": _json(_event_json(item.before)),
                "after": _json(_event_json(item.after)),
                "reason": item.reason,
                "conf": item.confidence,
                "conflicts": _json(
                    [
                        {
                            "kind": c.kind.value,
                            "message": c.message,
                            "severity": c.severity,
                        }
                        for c in item.conflicts
                    ]
                ),
                "links": _json([str(x) for x in item.links]),
                "seq": item.sequence,
                "status": item.status.value,
            },
        )


def _json(value) -> str | None:
    import json

    return json.dumps(value, ensure_ascii=False) if value is not None else None


async def load_plan(
    db: AsyncSession, user_id: UUID, plan_id: UUID
) -> Plan | None:
    row = (
        await db.execute(
            text(
                """
                SELECT conversation_id, origin_note, status, summary,
                       created_at, expires_at
                FROM core.plans WHERE id = :pid AND user_id = :uid
                """
            ),
            {"pid": str(plan_id), "uid": str(user_id)},
        )
    ).first()

    if row is None:
        return None

    plan = Plan(
        id=plan_id,
        user_id=user_id,
        conversation_id=row[0],
        origin_note=row[1],
        status=PlanStatus(row[2]),
        summary=row[3] or "",
        created_at=row[4],
        expires_at=row[5],
    )

    items = await db.execute(
        text(
            """
            SELECT id, target, op, target_ref, before, after, reason,
                   confidence, conflicts, links, sequence, status
            FROM core.plan_items
            WHERE plan_id = :pid AND user_id = :uid
            ORDER BY sequence
            """
        ),
        {"pid": str(plan_id), "uid": str(user_id)},
    )

    for r in items.all():
        plan.items.append(
            DiffItem(
                id=r[0],
                target=Target(r[1]),
                op=Op(r[2]),
                target_ref=r[3],
                before=_event_from_json(r[4]),
                after=_event_from_json(r[5]),
                reason=r[6],
                confidence=r[7] or 1.0,
                conflicts=tuple(
                    Conflict(
                        kind=ConflictKind(c["kind"]),
                        message=c["message"],
                        severity=c.get("severity", "warning"),
                    )
                    for c in (r[8] or [])
                ),
                links=tuple(UUID(x) for x in (r[9] or [])),
                sequence=r[10],
                status=ItemStatus(r[11]),
            )
        )

    return plan


async def mark_applied(
    db: AsyncSession, user_id: UUID, plan_id: UUID, result
) -> None:
    await db.execute(
        text(
            """
            UPDATE core.plans
            SET status = :status, applied_at = now()
            WHERE id = :pid AND user_id = :uid
            """
        ),
        {
            "pid": str(plan_id),
            "uid": str(user_id),
            "status": result.status.value,
        },
    )

    for record in result.applied:
        await db.execute(
            text(
                """
                UPDATE core.plan_items
                SET status = 'applied', applied_ref = :ref, applied_at = now()
                WHERE id = :iid AND user_id = :uid
                """
            ),
            {
                "iid": str(record.item_id),
                "uid": str(user_id),
                "ref": record.applied_ref,
            },
        )

    for item_id, error in result.failed:
        await db.execute(
            text(
                """
                UPDATE core.plan_items
                SET status = 'failed', error = :err
                WHERE id = :iid AND user_id = :uid
                """
            ),
            {"iid": str(item_id), "uid": str(user_id), "err": error[:500]},
        )


async def mark_rejected(
    db: AsyncSession, user_id: UUID, plan_id: UUID
) -> None:
    await db.execute(
        text(
            "UPDATE core.plans SET status = 'rejected' "
            "WHERE id = :pid AND user_id = :uid"
        ),
        {"pid": str(plan_id), "uid": str(user_id)},
    )
    await db.execute(
        text(
            "UPDATE core.plan_items SET status = 'rejected' "
            "WHERE plan_id = :pid AND user_id = :uid AND status = 'pending'"
        ),
        {"pid": str(plan_id), "uid": str(user_id)},
    )


async def record_undo_log(
    db: AsyncSession, user_id: UUID, plan_id: UUID, result
) -> None:
    """Snapshot do estado anterior, para o undo de 7 dias (§5.8)."""
    for record in result.applied:
        await db.execute(
            text(
                """
                INSERT INTO core.plan_undo_log
                  (user_id, plan_id, item_id, op, snapshot, applied_ref)
                VALUES (:uid, :pid, :iid, :op, CAST(:snap AS json), :ref)
                """
            ),
            {
                "uid": str(user_id),
                "pid": str(plan_id),
                "iid": str(record.item_id),
                "op": record.op.value,
                "snap": _json(_event_json(record.snapshot_before)),
                "ref": record.applied_ref,
            },
        )
