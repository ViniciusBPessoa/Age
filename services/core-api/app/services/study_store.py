"""Persistência de estudo e recalibração de estimativas.

ARQUITETURA.md §5.5 e §5.9.8. Duas responsabilidades:

1. **Materializar** um plano de estudo: assunto, tópicos e sessões,
   ligando cada sessão ao item de plano que a criou.
2. **Recalibrar** a estimativa quando o usuário registra o tempo real.

A recalibração é o que faz o produto melhorar com o uso. Sem ela, o
assistente repetiria para sempre o mesmo palpite errado — e a seção
§5.9.8 promete o contrário: *"Ajustei minhas estimativas: reduzi os
tópicos parecidos em ~20%"*.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.planner import StudyPlan, Topic

log = logging.getLogger("core-api.study_store")

# Quantas sessões concluídas bastam para confiar na média e recalibrar.
# Uma só pode ser atípica (interrupção, dia ruim); três já indicam
# tendência. Abaixo disso, mexer na estimativa seria ruído.
_MIN_SAMPLES = 2

# Teto do ajuste por rodada. Sem limite, uma sessão de 10 min (a pessoa
# desistiu no meio) cortaria a estimativa do tópico em 80% e o plano
# seguinte ficaria absurdo.
_MAX_ADJUST = 0.4


@dataclass(frozen=True)
class SubjectProgress:
    subject_id: UUID
    name: str
    deadline: datetime | None
    total_estimated: timedelta
    total_done: timedelta
    sessions_planned: int
    sessions_done: int
    sessions_skipped: int
    topics_total: int
    topics_completed: int

    @property
    def completion(self) -> float:
        if self.sessions_planned == 0:
            return 0.0
        return self.sessions_done / self.sessions_planned

    @property
    def is_behind(self) -> bool:
        """Mais de uma sessão pulada e nada reagendado ainda."""
        return self.sessions_skipped >= 1


# ---------------------------------------------------------------
# Materialização
# ---------------------------------------------------------------
async def persist_study_plan(
    db: AsyncSession,
    user_id: UUID,
    plan: StudyPlan,
    topics: list[Topic],
    *,
    plan_id: UUID | None = None,
    syllabus: str | None = None,
) -> UUID:
    """Grava assunto, tópicos e sessões. Devolve o id do assunto."""
    subject_id = uuid4()

    await db.execute(
        text(
            """
            INSERT INTO core.study_subjects
              (id, user_id, name, deadline, total_estimated_min,
               syllabus_text, plan_id)
            VALUES (:id, :uid, :name, :deadline, :total, :syllabus, :plan)
            """
        ),
        {
            "id": str(subject_id),
            "uid": str(user_id),
            "name": plan.subject,
            "deadline": plan.deadline,
            "total": int(plan.total_estimated.total_seconds() // 60),
            "syllabus": syllabus,
            "plan": str(plan_id) if plan_id else None,
        },
    )

    topic_ids: dict[str, UUID] = {}
    for topic in topics:
        topic_id = uuid4()
        topic_ids[topic.name] = topic_id
        await db.execute(
            text(
                """
                INSERT INTO core.study_topics
                  (id, user_id, subject_id, name, estimated_min,
                   order_index, prerequisites)
                VALUES (:id, :uid, :sid, :name, :est, :order,
                        CAST(:prereq AS json))
                """
            ),
            {
                "id": str(topic_id),
                "uid": str(user_id),
                "sid": str(subject_id),
                "name": topic.name,
                "est": int(topic.estimated.total_seconds() // 60),
                "order": topic.order,
                "prereq": _json(list(topic.prerequisites)),
            },
        )

    for session in plan.sessions:
        topic_id = topic_ids.get(session.topic_name)
        if topic_id is None:
            # Sessão de um tópico que não está na lista: não deveria
            # acontecer, mas descartar é melhor que gravar órfão.
            log.warning(
                "sessão de tópico desconhecido '%s' descartada",
                session.topic_name,
            )
            continue

        await db.execute(
            text(
                """
                INSERT INTO core.study_sessions
                  (user_id, topic_id, scheduled_start, scheduled_end,
                   kind, part, of_parts)
                VALUES (:uid, :tid, :start, :end, :kind, :part, :of)
                """
            ),
            {
                "uid": str(user_id),
                "tid": str(topic_id),
                "start": session.start,
                "end": session.end,
                "kind": session.kind.value,
                "part": session.part,
                "of": session.of_parts,
            },
        )

    return subject_id


async def link_sessions_to_events(
    db: AsyncSession, user_id: UUID, plan_id: UUID
) -> int:
    """Liga cada sessão ao evento que o `PlanExecutor` criou.

    Roda depois da aplicação: até ali as sessões existem mas não têm
    evento. O casamento é por horário porque o `plan_item_id` não
    atravessa o `StagingBuilder` (§5.2) — e horário é único o bastante
    dentro de um plano, já que sessões não se sobrepõem.
    """
    result = await db.execute(
        text(
            """
            UPDATE core.study_sessions s
            SET event_ref = e.id::text,
                plan_item_id = i.id
            FROM core.plan_items i
            JOIN core.local_events e ON e.plan_item_id IS NULL
                                    AND e.id::text = i.applied_ref
            WHERE i.plan_id = :pid
              AND i.user_id = :uid
              AND i.status = 'applied'
              AND s.user_id = :uid
              AND s.event_ref IS NULL
              AND s.scheduled_start = e.start_at
            """
        ),
        {"pid": str(plan_id), "uid": str(user_id)},
    )
    return result.rowcount or 0


# ---------------------------------------------------------------
# Registro de fato (§5.2, categoria "registro")
# ---------------------------------------------------------------
async def mark_session(
    db: AsyncSession,
    user_id: UUID,
    session_id: UUID,
    *,
    status: str,
    actual_min: int | None = None,
    difficulty: int | None = None,
    notes: str | None = None,
) -> dict:
    """Registra o que aconteceu com uma sessão.

    Isto NÃO é proposta de mudança: é fato sobre o passado. Tratar como
    plano forçaria o usuário a "aprovar" que ele faltou, o que é absurdo
    (§5.2). O que vem depois — reagendar a sessão perdida — é que entra
    no diff.
    """
    if status not in ("done", "skipped"):
        raise ValueError(f"status inválido: {status}")

    row = (
        await db.execute(
            text(
                """
                UPDATE core.study_sessions
                SET status = :status,
                    actual_min = COALESCE(:actual, actual_min),
                    difficulty_rating = COALESCE(:diff, difficulty_rating),
                    notes = COALESCE(:notes, notes),
                    recorded_at = now()
                WHERE id = :sid AND user_id = :uid
                RETURNING topic_id, kind, scheduled_start,
                          EXTRACT(EPOCH FROM (scheduled_end - scheduled_start))
                              / 60
                """
            ),
            {
                "sid": str(session_id),
                "uid": str(user_id),
                "status": status,
                "actual": actual_min,
                "diff": difficulty,
                "notes": notes,
            },
        )
    ).first()

    if row is None:
        raise LookupError("sessão não encontrada")

    topic_id, kind, scheduled_start, planned_min = row

    calibration = None
    if status == "done":
        calibration = await recalibrate_topic(db, user_id, topic_id)
        await _maybe_complete_topic(db, user_id, topic_id)

    streak = await _skip_streak(db, user_id, topic_id)

    return {
        "status": status,
        "topic_id": str(topic_id),
        "planned_min": int(planned_min or 0),
        "actual_min": actual_min,
        "calibration": calibration,
        # 3 faltas no mesmo assunto é o gatilho de §5.9.8: reagendar de
        # novo seria fingir que o plano funciona.
        "skip_streak": streak,
        "should_rethink": streak >= 3,
    }


async def recalibrate_topic(
    db: AsyncSession, user_id: UUID, topic_id: UUID
) -> dict | None:
    """Ajusta a estimativa do tópico pelo tempo real das sessões.

    Devolve o que mudou, para o assistente poder dizer ao usuário —
    calibração invisível não serve: ele precisa saber que o sistema
    aprende (§5.9.8).
    """
    row = (
        await db.execute(
            text(
                """
                SELECT
                  count(*) FILTER (WHERE actual_min IS NOT NULL),
                  sum(actual_min),
                  sum(EXTRACT(EPOCH FROM (scheduled_end - scheduled_start)) / 60)
                      FILTER (WHERE actual_min IS NOT NULL)
                FROM core.study_sessions
                WHERE topic_id = :tid AND user_id = :uid
                  AND status = 'done' AND kind = 'study'
                """
            ),
            {"tid": str(topic_id), "uid": str(user_id)},
        )
    ).first()

    if row is None:
        return None

    samples, total_actual, total_planned = row
    if not samples or samples < _MIN_SAMPLES or not total_planned:
        # Amostra pequena demais. Mexer na estimativa com uma sessão só
        # seria tomar ruído por tendência.
        return None

    ratio = float(total_actual) / float(total_planned)
    # Limita o ajuste: uma sessão de 10 min (desistiu no meio) não deve
    # cortar a estimativa em 80%.
    ratio = max(1 - _MAX_ADJUST, min(1 + _MAX_ADJUST, ratio))

    # CAST explícito em `:ratio`. Sem ele, `estimated_min * :ratio`
    # tenta inferir o tipo pelo da coluna (integer) e o Postgres recusa
    # ou trunca o parâmetro fracionário — 120 min × 0.67 gravava 15 em
    # vez de 80, e o plano seguinte ficava absurdo.
    updated = (
        await db.execute(
            text(
                """
                UPDATE core.study_topics
                SET calibrated_min = GREATEST(
                    15,
                    round(estimated_min * CAST(:ratio AS numeric))::integer
                )
                WHERE id = :tid AND user_id = :uid
                RETURNING name, estimated_min, calibrated_min
                """
            ),
            {"tid": str(topic_id), "uid": str(user_id), "ratio": ratio},
        )
    ).first()

    if updated is None:
        return None

    name, original, calibrated = updated
    return {
        "topic": name,
        "estimated_min": original,
        "calibrated_min": calibrated,
        "ratio": round(ratio, 2),
        "samples": samples,
        "direction": "faster" if ratio < 0.95 else (
            "slower" if ratio > 1.05 else "on_target"
        ),
    }


async def _maybe_complete_topic(
    db: AsyncSession, user_id: UUID, topic_id: UUID
) -> None:
    """Marca o tópico como concluído quando todas as sessões saíram."""
    await db.execute(
        text(
            """
            UPDATE core.study_topics t
            SET status = 'completed', completed_at = now()
            WHERE t.id = :tid AND t.user_id = :uid
              AND t.status <> 'completed'
              AND NOT EXISTS (
                  SELECT 1 FROM core.study_sessions s
                  WHERE s.topic_id = t.id AND s.kind = 'study'
                    AND s.status = 'planned'
              )
            """
        ),
        {"tid": str(topic_id), "uid": str(user_id)},
    )


async def _skip_streak(
    db: AsyncSession, user_id: UUID, topic_id: UUID
) -> int:
    """Quantas sessões consecutivas foram puladas NESTE assunto.

    Conta por assunto e não por tópico: pular três sessões de tópicos
    diferentes da mesma matéria é o mesmo sinal — o plano não está
    funcionando (§5.9.8).
    """
    row = (
        await db.execute(
            text(
                """
                WITH recent AS (
                    SELECT s.status
                    FROM core.study_sessions s
                    JOIN core.study_topics t ON t.id = s.topic_id
                    WHERE t.subject_id = (
                              SELECT subject_id FROM core.study_topics
                              WHERE id = :tid
                          )
                      AND s.user_id = :uid
                      AND s.status IN ('done','skipped')
                    ORDER BY s.recorded_at DESC NULLS LAST
                    LIMIT 10
                )
                SELECT count(*) FROM (
                    SELECT status,
                           row_number() OVER () AS rn,
                           sum(CASE WHEN status = 'done' THEN 1 ELSE 0 END)
                               OVER (ROWS UNBOUNDED PRECEDING) AS dones
                    FROM recent
                ) x
                WHERE dones = 0 AND status = 'skipped'
                """
            ),
            {"tid": str(topic_id), "uid": str(user_id)},
        )
    ).first()
    return int(row[0]) if row else 0


# ---------------------------------------------------------------
# Progresso
# ---------------------------------------------------------------
async def subject_progress(
    db: AsyncSession, user_id: UUID, subject_id: UUID
) -> SubjectProgress | None:
    row = (
        await db.execute(
            text(
                """
                SELECT
                  s.name, s.deadline, s.total_estimated_min,
                  (SELECT count(*) FROM core.study_sessions ss
                   JOIN core.study_topics tt ON tt.id = ss.topic_id
                   WHERE tt.subject_id = s.id AND ss.kind = 'study'),
                  (SELECT count(*) FROM core.study_sessions ss
                   JOIN core.study_topics tt ON tt.id = ss.topic_id
                   WHERE tt.subject_id = s.id AND ss.status = 'done'
                     AND ss.kind = 'study'),
                  (SELECT count(*) FROM core.study_sessions ss
                   JOIN core.study_topics tt ON tt.id = ss.topic_id
                   WHERE tt.subject_id = s.id AND ss.status = 'skipped'),
                  (SELECT coalesce(sum(ss.actual_min), 0)
                   FROM core.study_sessions ss
                   JOIN core.study_topics tt ON tt.id = ss.topic_id
                   WHERE tt.subject_id = s.id AND ss.status = 'done'),
                  (SELECT count(*) FROM core.study_topics tt
                   WHERE tt.subject_id = s.id),
                  (SELECT count(*) FROM core.study_topics tt
                   WHERE tt.subject_id = s.id AND tt.status = 'completed')
                FROM core.study_subjects s
                WHERE s.id = :sid AND s.user_id = :uid
                """
            ),
            {"sid": str(subject_id), "uid": str(user_id)},
        )
    ).first()

    if row is None:
        return None

    return SubjectProgress(
        subject_id=subject_id,
        name=row[0],
        deadline=row[1],
        total_estimated=timedelta(minutes=row[2] or 0),
        total_done=timedelta(minutes=row[6] or 0),
        sessions_planned=row[3] or 0,
        sessions_done=row[4] or 0,
        sessions_skipped=row[5] or 0,
        topics_total=row[7] or 0,
        topics_completed=row[8] or 0,
    )


async def list_subjects(db: AsyncSession, user_id: UUID) -> list[dict]:
    rows = await db.execute(
        text(
            """
            SELECT s.id, s.name, s.deadline, s.status,
                   s.total_estimated_min,
                   (SELECT count(*) FROM core.study_sessions ss
                    JOIN core.study_topics tt ON tt.id = ss.topic_id
                    WHERE tt.subject_id = s.id AND ss.kind = 'study'),
                   (SELECT count(*) FROM core.study_sessions ss
                    JOIN core.study_topics tt ON tt.id = ss.topic_id
                    WHERE tt.subject_id = s.id AND ss.status = 'done'
                      AND ss.kind = 'study')
            FROM core.study_subjects s
            WHERE s.user_id = :uid
            ORDER BY
              CASE WHEN s.status = 'active' THEN 0 ELSE 1 END,
              s.deadline NULLS LAST
            """
        ),
        {"uid": str(user_id)},
    )
    return [
        {
            "id": str(r[0]),
            "name": r[1],
            "deadline": r[2].isoformat() if r[2] else None,
            "status": r[3],
            "total_estimated_min": r[4],
            "sessions_planned": r[5],
            "sessions_done": r[6],
        }
        for r in rows.all()
    ]


async def pending_sessions(
    db: AsyncSession, user_id: UUID, *, limit: int = 20
) -> list[dict]:
    """Sessões que já passaram e ainda não foram marcadas.

    É o que alimenta a marcação rápida de §8.1 — abrir o chat para dizer
    "fui à academia" é atrito demais.
    """
    rows = await db.execute(
        text(
            """
            SELECT s.id, t.name, sub.name, s.scheduled_start,
                   s.scheduled_end, s.kind, s.part, s.of_parts
            FROM core.study_sessions s
            JOIN core.study_topics t ON t.id = s.topic_id
            JOIN core.study_subjects sub ON sub.id = t.subject_id
            WHERE s.user_id = :uid
              AND s.status = 'planned'
              AND s.scheduled_end < now()
            ORDER BY s.scheduled_start DESC
            LIMIT :lim
            """
        ),
        {"uid": str(user_id), "lim": limit},
    )
    return [
        {
            "id": str(r[0]),
            "topic": r[1],
            "subject": r[2],
            "scheduled_start": r[3].isoformat(),
            "scheduled_end": r[4].isoformat(),
            "kind": r[5],
            "part": r[6],
            "of_parts": r[7],
        }
        for r in rows.all()
    ]


# ---------------------------------------------------------------
# Preferências aprendidas (§5.9.7)
# ---------------------------------------------------------------
async def save_learned_preference(
    db: AsyncSession,
    user_id: UUID,
    key: str,
    value: object,
    *,
    source: str = "chat",
    conversation_id: UUID | None = None,
    confirmed: bool = False,
) -> None:
    """Transforma correção pontual em preferência persistente.

    "Nada antes das 10h" dito numa conversa deve valer para sempre, se
    o usuário confirmar. Sem isto ele corrige a mesma coisa toda semana.
    """
    await db.execute(
        text(
            """
            INSERT INTO core.learned_preferences
              (user_id, key, value, source, conversation_id, confirmed)
            VALUES (:uid, :key, CAST(:value AS json), :source, :cid, :conf)
            ON CONFLICT (user_id, key) DO UPDATE
              SET value = EXCLUDED.value,
                  source = EXCLUDED.source,
                  conversation_id = EXCLUDED.conversation_id,
                  confirmed = EXCLUDED.confirmed,
                  created_at = now()
            """
        ),
        {
            "uid": str(user_id),
            "key": key,
            "value": _json(value),
            "source": source,
            "cid": str(conversation_id) if conversation_id else None,
            "conf": confirmed,
        },
    )


async def learned_preferences(
    db: AsyncSession, user_id: UUID, *, only_confirmed: bool = True
) -> dict:
    clause = "AND confirmed = true" if only_confirmed else ""
    rows = await db.execute(
        text(
            f"""
            SELECT key, value FROM core.learned_preferences
            WHERE user_id = :uid {clause}
            """
        ),
        {"uid": str(user_id)},
    )
    return {r[0]: r[1] for r in rows.all()}


def _json(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)
