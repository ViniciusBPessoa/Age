"""Persistência de rotinas, materialização e relatório.

ARQUITETURA.md §5.9.4, §5.9.9 e §5.10. Três responsabilidades:

1. **Materializar** ocorrências a partir da RRULE. Por que materializar
   em vez de derivar na leitura: a ocorrência registra o que
   *aconteceu* (feito, pulado, movido), e isso não é função da regra de
   recorrência. Uma sessão movida de terça para quinta continua sendo a
   ocorrência de terça — se a aderência fosse derivada da RRULE, ela
   apareceria como "faltou terça, fez quinta a mais".

2. **Relatar** o período: planejado vs. realizado, cruzando estudo,
   rotina e agenda. Sem tabela de agregado, de propósito — manter um
   agregado em sincronia com três fontes é mais caro e mais frágil que
   somar na leitura, no volume de um usuário pessoal.

3. **Registrar mudança externa** (§5.10), que não gera notificação e é
   reconhecida uma vez, na próxima abertura do chat.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.routines import (
    Adherence,
    Occurrence,
    OccurrenceStatus,
    Routine,
    expand,
    find_skip_patterns,
    format_moment,
    summarize_adherence,
)
from app.ports.calendar import Flexibility

log = logging.getLogger("core-api.routine_store")

LOCAL_EVENTS = "local_events"
SHADOW_EVENTS = "events_shadow"


# ---------------------------------------------------------------
# Rotina
# ---------------------------------------------------------------
async def create_routine(
    db: AsyncSession,
    user_id: UUID,
    *,
    title: str,
    rrule: str,
    duration_min: int,
    started_at: datetime,
    flexibility: str = "flexible",
    target_per_week: int | None = None,
    category: str | None = None,
) -> UUID:
    """Grava a série. A materialização é separada, e por um motivo.

    Criar a rotina e materializar as ocorrências são passos distintos
    porque a materialização pode gerar conflito — e o usuário precisa
    ver o conflito antes de os eventos existirem na agenda (§5.7). A
    série é o compromisso; as ocorrências passam por aprovação.
    """
    # Valida a RRULE antes de gravar, no domínio. Gravar uma regra
    # inválida produziria uma série que nunca materializa nada e um
    # painel de aderência vazio sem explicação.
    probe = Routine(
        id=None,
        title=title,
        rrule=rrule,
        duration=timedelta(minutes=duration_min),
        started_at=started_at,
        flexibility=Flexibility(flexibility),
        target_per_week=target_per_week,
    )
    expand(
        probe,
        window_start=started_at,
        window_end=started_at + timedelta(days=28),
        limit=1,
    )

    row = (
        await db.execute(
            text(
                """
                INSERT INTO core.routines
                  (user_id, title, rrule, duration_min, flexibility,
                   target_per_week, category, started_at)
                VALUES (:uid, :title, :rrule, :dur, :flex,
                        CAST(:target AS integer), :cat, :started)
                RETURNING id
                """
            ),
            {
                "uid": str(user_id),
                "title": title,
                "rrule": rrule,
                "dur": duration_min,
                "flex": flexibility,
                "target": target_per_week,
                "cat": category,
                "started": started_at,
            },
        )
    ).scalar_one()
    return row


async def load_routines(
    db: AsyncSession, user_id: UUID, *, include_ended: bool = False
) -> list[Routine]:
    rows = await db.execute(
        text(
            """
            SELECT id, title, rrule, duration_min, flexibility,
                   target_per_week, category, started_at, paused_at,
                   ended_at
            FROM core.routines
            WHERE user_id = :uid
              AND (:include_ended OR ended_at IS NULL)
            ORDER BY created_at
            """
        ),
        {"uid": str(user_id), "include_ended": include_ended},
    )
    return [_to_routine(r) for r in rows.all()]


async def load_routine(
    db: AsyncSession, user_id: UUID, routine_id: UUID
) -> Routine | None:
    row = (
        await db.execute(
            text(
                """
                SELECT id, title, rrule, duration_min, flexibility,
                       target_per_week, category, started_at, paused_at,
                       ended_at
                FROM core.routines
                WHERE id = :rid AND user_id = :uid
                """
            ),
            {"rid": str(routine_id), "uid": str(user_id)},
        )
    ).first()
    return _to_routine(row) if row else None


def _to_routine(row) -> Routine:
    return Routine(
        id=str(row[0]),
        title=row[1],
        rrule=row[2],
        duration=timedelta(minutes=row[3]),
        flexibility=Flexibility(row[4]),
        target_per_week=row[5],
        category=row[6],
        started_at=row[7],
        paused_at=row[8],
        ended_at=row[9],
    )


async def set_routine_state(
    db: AsyncSession,
    user_id: UUID,
    routine_id: UUID,
    *,
    paused: bool | None = None,
    ended: bool = False,
) -> bool:
    """Pausa, retoma ou encerra. Nunca apaga.

    §5.9.4: pausar não apaga, porque a aderência histórica continua
    valendo e retomar é um clique. Apagar perderia o histórico, que é
    justamente o que dá valor ao painel.
    """
    if ended:
        clause = "ended_at = now()"
    elif paused is True:
        clause = "paused_at = now()"
    elif paused is False:
        clause = "paused_at = NULL"
    else:
        return False

    result = await db.execute(
        text(
            f"UPDATE core.routines SET {clause} "
            "WHERE id = :rid AND user_id = :uid"
        ),
        {"rid": str(routine_id), "uid": str(user_id)},
    )
    return (result.rowcount or 0) > 0


# ---------------------------------------------------------------
# Materialização
# ---------------------------------------------------------------
async def materialize(
    db: AsyncSession,
    user_id: UUID,
    routine: Routine,
    *,
    window_start: datetime,
    window_end: datetime,
) -> int:
    """Cria as linhas de ocorrência da janela. Idempotente.

    O `ON CONFLICT DO NOTHING` sobre `(routine_id, scheduled_start)` é o
    que permite rodar isto a cada abertura da timeline sem duplicar
    aderência — e rodar de novo depois de uma falha parcial.

    Ocorrências JÁ REGISTRADAS não são tocadas: se a pessoa marcou
    quarta como pulada, rematerializar não pode devolver a linha para
    `planned`. É o que o `DO NOTHING` garante, e é mais importante que
    parecer.
    """
    occurrences = expand(
        routine, window_start=window_start, window_end=window_end
    )
    if not occurrences:
        return 0

    created = 0
    for occurrence in occurrences:
        result = await db.execute(
            text(
                """
                INSERT INTO core.routine_occurrences
                  (user_id, routine_id, scheduled_start, scheduled_end)
                VALUES (:uid, :rid, :start, :end)
                ON CONFLICT (routine_id, scheduled_start) DO NOTHING
                """
            ),
            {
                "uid": str(user_id),
                "rid": routine.id,
                "start": occurrence.start,
                "end": occurrence.end,
            },
        )
        created += result.rowcount or 0

    return created


async def load_occurrences(
    db: AsyncSession,
    user_id: UUID,
    *,
    routine_id: UUID | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> list[Occurrence]:
    rows = await db.execute(
        text(
            """
            SELECT o.routine_id, r.title, o.scheduled_start,
                   o.scheduled_end, o.status, o.actual_min, o.moved_to,
                   o.event_ref_kind, o.event_ref_id
            FROM core.routine_occurrences o
            JOIN core.routines r ON r.id = o.routine_id
            WHERE o.user_id = :uid
              AND (CAST(:rid AS uuid) IS NULL
                   OR o.routine_id = CAST(:rid AS uuid))
              AND (CAST(:ws AS timestamptz) IS NULL
                   OR o.scheduled_start >= CAST(:ws AS timestamptz))
              AND (CAST(:we AS timestamptz) IS NULL
                   OR o.scheduled_start < CAST(:we AS timestamptz))
            ORDER BY o.scheduled_start
            """
        ),
        {
            "uid": str(user_id),
            "rid": str(routine_id) if routine_id else None,
            "ws": window_start,
            "we": window_end,
        },
    )
    return [
        Occurrence(
            routine_id=str(r[0]),
            routine_title=r[1],
            start=r[2],
            end=r[3],
            status=OccurrenceStatus(r[4]),
            actual_min=r[5],
            moved_to=r[6],
            event_ref_kind=r[7],
            event_ref_id=r[8],
        )
        for r in rows.all()
    ]


async def mark_occurrence(
    db: AsyncSession,
    user_id: UUID,
    *,
    routine_id: UUID,
    scheduled_start: datetime,
    status: str,
    actual_min: int | None = None,
    notes: str | None = None,
    moved_to: datetime | None = None,
) -> bool:
    """Registra o que aconteceu com uma ocorrência.

    Categoria "registro de fato" de §5.2: executa de verdade e não
    passa por diff. "Pulei a academia ontem" é fato sobre o passado,
    não proposta de mudança — tratar como plano forçaria o usuário a
    "aprovar" que faltou.
    """
    result = await db.execute(
        text(
            """
            UPDATE core.routine_occurrences
            SET status = :status,
                actual_min = CAST(:actual AS integer),
                notes = COALESCE(:notes, notes),
                moved_to = CAST(:moved AS timestamptz),
                recorded_at = now()
            WHERE user_id = :uid
              AND routine_id = :rid
              AND scheduled_start = :start
            """
        ),
        {
            "uid": str(user_id),
            "rid": str(routine_id),
            "start": scheduled_start,
            "status": status,
            "actual": actual_min,
            "notes": notes,
            "moved": moved_to,
        },
    )
    return (result.rowcount or 0) > 0


async def adherence_for(
    db: AsyncSession,
    user_id: UUID,
    routine: Routine,
    *,
    period_start: datetime,
    period_end: datetime,
) -> Adherence:
    occurrences = await load_occurrences(
        db,
        user_id,
        routine_id=UUID(routine.id) if routine.id else None,
        window_start=period_start,
        window_end=period_end,
    )
    return summarize_adherence(
        routine,
        occurrences,
        period_start=period_start,
        period_end=period_end,
    )


# ---------------------------------------------------------------
# Relatório do período (§5.9.9)
# ---------------------------------------------------------------
@dataclass(frozen=True)
class StudyTotals:
    planned: int
    done: int
    skipped: int
    planned_minutes: int
    done_minutes: int

    @property
    def completion(self) -> float:
        if self.planned_minutes == 0:
            return 0.0
        return self.done_minutes / self.planned_minutes


@dataclass(frozen=True)
class PeriodReport:
    """Planejado vs. realizado, com o padrão quando houver.

    §5.9.9: dado antes de interpretação, e interpretação só quando o
    dado sustenta. Este tipo carrega só números e padrões verificados —
    a redação é do assistente, e o system prompt proíbe elogio que o
    dado não sustenta.
    """

    period_start: datetime
    period_end: datetime
    study: StudyTotals
    routines: tuple[Adherence, ...]
    events_total: int
    patterns: tuple[str, ...]
    # O fuso viaja com o relatório para `describe_report` não precisar
    # recebê-lo de novo em cada chamador — e para ninguém esquecer de
    # passar e voltar a imprimir UTC.
    timezone_name: str = "America/Sao_Paulo"

    @property
    def has_anything(self) -> bool:
        return (
            self.study.planned > 0
            or any(a.planned > 0 for a in self.routines)
            or self.events_total > 0
        )


async def period_report(
    db: AsyncSession,
    user_id: UUID,
    *,
    period_start: datetime,
    period_end: datetime,
    timezone_name: str = "America/Sao_Paulo",
) -> PeriodReport:
    study_row = (
        await db.execute(
            text(
                """
                SELECT
                  count(*) AS planned,
                  count(*) FILTER (WHERE s.status = 'done') AS done,
                  count(*) FILTER (WHERE s.status = 'skipped') AS skipped,
                  COALESCE(SUM(
                    EXTRACT(EPOCH FROM
                      (s.scheduled_end - s.scheduled_start)) / 60
                  )::integer, 0) AS planned_min,
                  COALESCE(SUM(s.actual_min) FILTER (
                    WHERE s.status = 'done'
                  ), 0) AS done_min
                FROM core.study_sessions s
                WHERE s.user_id = :uid
                  AND s.scheduled_start >= :ps
                  AND s.scheduled_start < :pe
                """
            ),
            {"uid": str(user_id), "ps": period_start, "pe": period_end},
        )
    ).first()

    study = StudyTotals(
        planned=study_row[0] or 0,
        done=study_row[1] or 0,
        skipped=study_row[2] or 0,
        planned_minutes=study_row[3] or 0,
        done_minutes=study_row[4] or 0,
    )

    routines = await load_routines(db, user_id, include_ended=True)
    adherences = []
    for routine in routines:
        entry = await adherence_for(
            db,
            user_id,
            routine,
            period_start=period_start,
            period_end=period_end,
        )
        # Rotina sem nenhuma ocorrência no período não entra: linha com
        # zeros no relatório é ruído, não informação.
        if entry.planned > 0:
            adherences.append(entry)

    events_total = (
        await db.execute(
            text(
                """
                SELECT count(*) FROM core.local_events
                WHERE user_id = :uid AND deleted_at IS NULL
                  AND start_at >= :ps AND start_at < :pe
                """
            ),
            {"uid": str(user_id), "ps": period_start, "pe": period_end},
        )
    ).scalar_one()

    patterns = await _patterns_for_period(
        db,
        user_id,
        period_start=period_start,
        period_end=period_end,
        timezone_name=timezone_name,
    )

    return PeriodReport(
        period_start=period_start,
        period_end=period_end,
        study=study,
        routines=tuple(adherences),
        events_total=events_total,
        patterns=tuple(patterns),
        timezone_name=timezone_name,
    )


async def _patterns_for_period(
    db: AsyncSession,
    user_id: UUID,
    *,
    period_start: datetime,
    period_end: datetime,
    timezone_name: str,
) -> list[str]:
    """Procura padrão no que foi pulado, cruzando estudo e rotina.

    Cruzar as duas fontes importa: "você pula o que é de manhã" é uma
    observação sobre a pessoa, não sobre o estudo. Olhar só as sessões
    de estudo acharia metade do padrão, ou nenhum, por falta de
    amostras.
    """
    # Sessões de estudo, como ocorrências — o detector de padrão
    # trabalha sobre a mesma forma, e assim há uma regra só.
    rows = await db.execute(
        text(
            """
            SELECT s.scheduled_start, s.scheduled_end, s.status,
                   s.actual_min
            FROM core.study_sessions s
            WHERE s.user_id = :uid
              AND s.scheduled_start >= :ps AND s.scheduled_start < :pe
              AND s.status IN ('done', 'skipped')
            """
        ),
        {"uid": str(user_id), "ps": period_start, "pe": period_end},
    )
    pseudo = [
        Occurrence(
            routine_id=None,
            routine_title="estudo",
            start=r[0],
            end=r[1],
            status=OccurrenceStatus(r[2]),
            actual_min=r[3],
        )
        for r in rows.all()
    ]

    occurrences = await load_occurrences(
        db, user_id, window_start=period_start, window_end=period_end
    )

    from app.domain.routines import describe_pattern

    found = find_skip_patterns(
        [*pseudo, *occurrences], timezone_name=timezone_name
    )
    # No máximo dois: §5.9.12 pede resposta curta, e três observações
    # numa resposta fazem o usuário parar de ler.
    return [describe_pattern(p) for p in found[:2]]


def describe_report(report: PeriodReport) -> str:
    """Texto do relatório, em bloco monoespaçado (§5.9.12).

    Formato pensado para o modelo citar sem recalcular. Note o que NÃO
    está aqui: nenhuma frase de elogio, nenhum "parabéns". §5.9.9 é
    explícito — elogio que não corresponde ao dado destrói a confiança
    no resto.
    """
    if not report.has_anything:
        start = _local(report.period_start, report.timezone_name)
        end = _local(report.period_end, report.timezone_name)
        return (
            f"Semana de {start:%d/%m} a {end:%d/%m}: "
            "nada planejado nem registrado."
        )

    start = _local(report.period_start, report.timezone_name)
    end = _local(report.period_end, report.timezone_name)
    lines = [
        f"Período de {start:%d/%m} a {end:%d/%m}:",
        "",
    ]

    if report.study.planned:
        hours_done = report.study.done_minutes / 60
        hours_planned = report.study.planned_minutes / 60
        pct = round(report.study.completion * 100)
        lines.append(
            f"Estudo      {report.study.planned} sessões planejadas · "
            f"{report.study.done} feitas · {report.study.skipped} puladas"
        )
        lines.append(
            f"            {hours_done:.1f}h de {hours_planned:.1f}h  ({pct}%)"
        )

    for entry in report.routines:
        label = entry.routine_title[:11].ljust(11)
        detail = f"{entry.planned} planejadas · {entry.done} feitas"
        if entry.skipped:
            detail += f" · {entry.skipped} puladas"
        if entry.moved:
            detail += f" · {entry.moved} movidas"
        lines.append(f"{label} {detail}")

    if report.events_total:
        lines.append(f"Agenda      {report.events_total} compromissos")

    if report.patterns:
        lines.append("")
        for pattern in report.patterns:
            lines.append(pattern.capitalize() + ".")

    return "\n".join(lines)


# ---------------------------------------------------------------
# Mudança externa (§5.10)
# ---------------------------------------------------------------
async def record_external_change(
    db: AsyncSession,
    user_id: UUID,
    *,
    change_kind: str,
    title: str | None,
    starts_at: datetime | None,
    ends_at: datetime | None,
    event_ref_kind: str | None = None,
    event_ref_id: str | None = None,
) -> UUID | None:
    """Registra que algo mudou fora do Age, e com o que colide.

    **Não** manda mensagem: o assistente não inicia conversa (§1.2). O
    registro fica aqui e é reconhecido na próxima abertura do chat.

    O conflito é calculado AGORA, não na leitura: "apareceu uma reunião
    que bate com a sessão de Álgebra Linear" exige saber o que havia no
    horário no momento da detecção. Calcular depois daria a resposta
    errada se a sessão já tiver sido movida.
    """
    conflict_kind = conflict_id = conflict_title = None

    if starts_at is not None and ends_at is not None:
        clash = (
            await db.execute(
                text(
                    """
                    SELECT id, title FROM core.local_events
                    WHERE user_id = :uid
                      AND deleted_at IS NULL
                      AND created_by_age
                      AND start_at < :ends
                      AND end_at > :starts
                      AND (CAST(:ref AS text) IS NULL
                           OR CAST(id AS text) <> CAST(:ref AS text))
                    ORDER BY start_at
                    LIMIT 1
                    """
                ),
                {
                    "uid": str(user_id),
                    "starts": starts_at,
                    "ends": ends_at,
                    "ref": event_ref_id,
                },
            )
        ).first()
        if clash is not None:
            conflict_kind = LOCAL_EVENTS
            conflict_id = str(clash[0])
            conflict_title = clash[1]

    row = (
        await db.execute(
            text(
                """
                INSERT INTO core.external_change_log
                  (user_id, change_kind, event_ref_kind, event_ref_id,
                   title, starts_at, ends_at,
                   conflicts_with_kind, conflicts_with_id,
                   conflicts_with_title)
                VALUES (:uid, :kind, :ek, :eid, :title,
                        CAST(:starts AS timestamptz),
                        CAST(:ends AS timestamptz),
                        :ck, :ci, :ct)
                RETURNING id
                """
            ),
            {
                "uid": str(user_id),
                "kind": change_kind,
                "ek": event_ref_kind,
                "eid": event_ref_id,
                "title": title,
                "starts": starts_at,
                "ends": ends_at,
                "ck": conflict_kind,
                "ci": conflict_id,
                "ct": conflict_title,
            },
        )
    ).scalar_one()
    return row


@dataclass(frozen=True)
class ExternalChange:
    id: UUID
    change_kind: str
    title: str | None
    starts_at: datetime | None
    ends_at: datetime | None
    conflicts_with_title: str | None
    detected_at: datetime

    @property
    def is_conflict(self) -> bool:
        return self.conflicts_with_title is not None


async def pending_external_changes(
    db: AsyncSession, user_id: UUID, *, limit: int = 5
) -> list[ExternalChange]:
    """Mudanças ainda não reconhecidas, conflito primeiro.

    O limite existe porque §5.9.12 pede resposta curta: anunciar oito
    mudanças ao abrir o chat é o mesmo que não anunciar nenhuma.
    """
    rows = await db.execute(
        text(
            """
            SELECT id, change_kind, title, starts_at, ends_at,
                   conflicts_with_title, detected_at
            FROM core.external_change_log
            WHERE user_id = :uid AND acknowledged_at IS NULL
            ORDER BY (conflicts_with_title IS NULL), detected_at DESC
            LIMIT :lim
            """
        ),
        {"uid": str(user_id), "lim": limit},
    )
    return [
        ExternalChange(
            id=r[0],
            change_kind=r[1],
            title=r[2],
            starts_at=r[3],
            ends_at=r[4],
            conflicts_with_title=r[5],
            detected_at=r[6],
        )
        for r in rows.all()
    ]


async def acknowledge_external_changes(
    db: AsyncSession, user_id: UUID, ids: list[UUID] | None = None
) -> int:
    """Marca como reconhecidas. É o que impede a repetição.

    Sem isto, a mesma reunião seria anunciada em toda mensagem — e §5.10
    deixaria de ser "reativo" para ser exatamente o app que persegue o
    usuário.
    """
    if ids is not None and not ids:
        return 0

    if ids is None:
        result = await db.execute(
            text(
                "UPDATE core.external_change_log SET acknowledged_at = now() "
                "WHERE user_id = :uid AND acknowledged_at IS NULL"
            ),
            {"uid": str(user_id)},
        )
    else:
        result = await db.execute(
            text(
                """
                UPDATE core.external_change_log
                SET acknowledged_at = now()
                WHERE user_id = :uid AND acknowledged_at IS NULL
                  AND CAST(id AS text) IN (
                        SELECT json_array_elements_text(CAST(:ids AS json)))
                """
            ),
            {
                "uid": str(user_id),
                "ids": _json([str(x) for x in ids]),
            },
        )
    return result.rowcount or 0


def describe_changes(
    changes: list[ExternalChange],
    *,
    timezone_name: str = "America/Sao_Paulo",
) -> str:
    """O texto que abre a conversa (§5.10).

    Conflito primeiro e com o que colide nomeado — é a diferença entre
    informação e ruído.

    O fuso é parâmetro porque os horários vêm do banco em UTC, e dizer
    "terça 22:00" para um evento das 19:00 faria o usuário achar que o
    sistema está errado sobre a própria agenda dele.
    """
    if not changes:
        return ""

    lines: list[str] = []
    for change in changes:
        when = (
            format_moment(change.starts_at, timezone_name=timezone_name)
            if change.starts_at
            else "sem horário"
        )
        verb = {
            "created": "apareceu",
            "moved": "mudou de horário",
            "deleted": "foi removido",
            "updated": "foi alterado",
        }.get(change.change_kind, "mudou")

        entry = f"{verb}: '{change.title or 'sem título'}' ({when})"
        if change.conflicts_with_title:
            entry += (
                f" — bate com '{change.conflicts_with_title}', "
                "que a gente marcou"
            )
        lines.append(entry)

    return "\n".join(lines)


def _local(moment: datetime, timezone_name: str) -> datetime:
    from zoneinfo import ZoneInfo

    try:
        return moment.astimezone(ZoneInfo(timezone_name))
    except Exception:  # noqa: BLE001
        return moment.astimezone(ZoneInfo("America/Sao_Paulo"))


def _json(value) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
