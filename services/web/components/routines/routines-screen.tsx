/**
 * Painel de rotinas (§8.1).
 *
 * "Séries ativas com aderência das últimas 4 semanas (`3/3`, `2/3`),
 * botão de pausar sem apagar, e marca de flexível/fixa.
 * Deliberadamente **sem streak nem medalha** — a decisão de §5.9.4: o
 * produto mostra o fato, não gamifica."
 *
 * A ausência é a feature. Streak transforma uma falta em perda de
 * progresso, e aí um dia ruim faz a pessoa abandonar a série inteira —
 * o oposto do que o produto deveria causar. Pelo mesmo raciocínio,
 * **movida não conta como falta**: quem mudou a academia de terça para
 * quinta e foi, cumpriu. Por isso `moved` aparece separado de
 * `skipped`, e não somado nele.
 *
 * O "pausar sem apagar" resolve um problema concreto: viagem de duas
 * semanas. Sem pausa, as duas opções são apagar a série (e perder o
 * histórico) ou deixá-la acumular faltas que não são faltas.
 */

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { addDays } from "date-fns";
import { useState } from "react";

import {
  Badge,
  Button,
  IconCheck,
  IconChevronDown,
  IconClock,
  IconLock,
  IconPause,
  IconPlay,
  IconRoutine,
  IconSkip,
  Empty,
  LoadError,
  PageHeader,
  Panel,
  SkeletonList,
  Spinner,
  cx,
} from "@/components/ui";
import { api } from "@/lib/api-client";
import { duration, moment, percent, range } from "@/lib/format";
import type { Adherence, Flexibility, Occurrence, Routine } from "@/lib/types";

const FLEX_BADGE: Record<
  Flexibility,
  { label: string; tone: "fixed" | "brand" | "neutral"; lock?: boolean }
> = {
  // O cadeado é ícone, não emoji: §5.6 usa a mesma marca na timeline, e
  // as duas precisam ser a mesma coisa para a pessoa ligar uma à outra.
  fixed: { label: "fixa", tone: "fixed", lock: true },
  semi_flexible: { label: "semi-flexível", tone: "neutral" },
  flexible: { label: "flexível", tone: "brand" },
};

export function RoutinesScreen({ timezone }: { timezone: string }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState<string | null>(null);

  const routines = useQuery({
    queryKey: ["routines"],
    // `include_ended`, não `include_paused`: pausada continua ativa
    // para a rota (§5.9.4 — pausar não encerra), e só a encerrada fica
    // fora por padrão. Pedir um parâmetro que não existe seria
    // ignorado em silêncio pelo FastAPI.
    queryFn: () => api.get<Routine[]>("/routines", { include_ended: false }),
  });

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ["routines"] });
  }

  return (
    <div className="min-h-dvh pb-16 md:pb-0">
      <PageHeader
        title="Rotinas"
        subtitle="Sem ofensiva e sem medalha, de propósito: o painel mostra o fato."
      />

      <div className="mx-auto max-w-3xl space-y-3 px-4 py-6 md:px-7">
        {routines.isError ? (
          <LoadError
            message="Não consegui carregar suas rotinas."
            onRetry={() => void routines.refetch()}
          />
        ) : routines.isLoading ? (
          <SkeletonList rows={2} />
        ) : (routines.data?.length ?? 0) === 0 ? (
          <Empty
            title="Nenhuma rotina ainda."
            icon={<IconRoutine className="h-5 w-5" />}
          >
            Peça no chat: &ldquo;quero academia 3x por semana&rdquo;. O Age simula a
            série, mostra onde ela bate com prova e deixa você escolher o horário antes
            de criar nada.
          </Empty>
        ) : (
          <ul className="space-y-3">
            {routines.data?.map((routine) => (
              <RoutineCard
                key={routine.id}
                routine={routine}
                timezone={timezone}
                expanded={open === routine.id}
                onToggle={() =>
                  setOpen((current) => (current === routine.id ? null : routine.id))
                }
                onChanged={refresh}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------
function RoutineCard({
  routine,
  timezone,
  expanded,
  onToggle,
  onChanged,
}: {
  routine: Routine;
  timezone: string;
  expanded: boolean;
  onToggle: () => void;
  onChanged: () => void;
}) {
  const paused = routine.paused_at !== null;
  const flex = FLEX_BADGE[routine.flexibility];

  // 28 dias: as "últimas 4 semanas" de §8.1.
  const adherence = useQuery({
    queryKey: ["routines", routine.id, "adherence"],
    queryFn: () =>
      api.get<Adherence>(`/routines/${routine.id}/adherence`, { days: 28 }),
  });

  const occurrences = useQuery({
    queryKey: ["routines", routine.id, "occurrences"],
    queryFn: () =>
      // A rota recebe janela (`from`/`to`), não `limit`. Quatro
      // semanas para trás e duas para frente: o passado é o que se
      // marca, o futuro é o que se confere.
      api.get<Occurrence[]>(`/routines/${routine.id}/occurrences`, {
        from: addDays(new Date(), -28).toISOString(),
        to: addDays(new Date(), 14).toISOString(),
      }),
    enabled: expanded,
  });

  // `paused` é query param, não corpo: a rota é
  // `PATCH /routines/{id}?paused=true`. Mandar no corpo faria o
  // FastAPI usar o default `None`, e `set_routine_state` não mudaria
  // nada — o botão pareceria funcionar e a série seguiria como estava.
  const togglePause = useMutation({
    mutationFn: () => api.patch(`/routines/${routine.id}?paused=${!paused}`),
    onSuccess: onChanged,
  });

  return (
    <Panel as="li" className={cx("overflow-hidden", paused && "opacity-75")}>
      <div className="px-4 py-4 md:px-5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="min-w-0 flex-1 truncate text-base font-semibold">
            {routine.title}
          </span>
          <Badge tone={flex.tone}>
            {flex.lock ? <IconLock className="h-2.5 w-2.5" /> : null}
            {flex.label}
          </Badge>
          {paused ? <Badge tone="neutral">pausada</Badge> : null}
        </div>

        <p className="mt-1.5 flex flex-wrap items-center gap-x-1.5 text-xs text-muted">
          <IconClock className="h-3.5 w-3.5 shrink-0" />
          {describeRule(routine.rrule)} · {duration(routine.duration_min)}
          {routine.next_occurrence && !paused ? (
            <> · próxima {moment(routine.next_occurrence, timezone)}</>
          ) : null}
        </p>

        {/* A aderência. `3/3` e `2/3` como §8.1 escreve — número puro,
            sem ofensiva, sem medalha, sem cor de reprovação. */}
        {adherence.data ? (
          <div className="mt-4 rounded-xl bg-surface-2 px-3.5 py-3">
            <div className="flex flex-wrap items-end gap-x-5 gap-y-2">
              <div>
                <span className="tnum text-2xl font-semibold leading-none">
                  {adherence.data.done}
                  <span className="text-base font-normal text-muted">
                    /{adherence.data.planned}
                  </span>
                </span>
                <p className="mt-1 text-2xs text-muted">nas últimas 4 semanas</p>
              </div>

              {/* `moved` separado de `skipped` de propósito: adaptar
                  não é falhar (§5.9.4). Ter colunas próprias é o que
                  deixa isso visível em vez de escrito. */}
              {adherence.data.moved > 0 ? (
                <div>
                  <span className="tnum text-base font-semibold text-brand">
                    {adherence.data.moved}
                  </span>
                  <p className="mt-0.5 text-2xs text-muted">
                    movida{adherence.data.moved === 1 ? "" : "s"}
                  </p>
                </div>
              ) : null}

              {adherence.data.skipped > 0 ? (
                <div>
                  <span className="tnum text-base font-semibold text-ink-2">
                    {adherence.data.skipped}
                  </span>
                  <p className="mt-0.5 text-2xs text-muted">
                    pulada{adherence.data.skipped === 1 ? "" : "s"}
                  </p>
                </div>
              ) : null}

              {adherence.data.target_per_week ? (
                <div className="ml-auto text-right">
                  <span className="tnum text-base font-semibold">
                    {adherence.data.per_week.toFixed(1)}x
                  </span>
                  <p className="mt-0.5 text-2xs text-muted">
                    meta {adherence.data.target_per_week}x/semana
                  </p>
                </div>
              ) : null}
            </div>
          </div>
        ) : null}

        <div className="mt-3.5 flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="secondary"
            onClick={onToggle}
            aria-expanded={expanded}
          >
            {expanded ? "esconder" : "ver ocorrências"}
            <IconChevronDown
              className={cx(
                "h-3.5 w-3.5 transition-transform duration-200",
                expanded && "rotate-180",
              )}
            />
          </Button>
          <Button
            size="sm"
            variant="ghost"
            loading={togglePause.isPending}
            onClick={() => togglePause.mutate()}
          >
            {togglePause.isPending ? null : paused ? (
              <IconPlay className="h-3.5 w-3.5" />
            ) : (
              <IconPause className="h-3.5 w-3.5" />
            )}
            {paused ? "retomar" : "pausar"}
          </Button>
          {!paused ? (
            <span className="text-2xs text-faint">pausar não apaga o histórico</span>
          ) : null}
        </div>
      </div>

      {expanded ? (
        <div className="animate-fade-in border-t border-line bg-surface-2/40 px-4 py-3.5 md:px-5">
          {occurrences.isLoading ? (
            <p className="flex items-center gap-2 text-xs text-muted">
              <Spinner /> carregando…
            </p>
          ) : (occurrences.data?.length ?? 0) === 0 ? (
            <p className="text-xs text-muted">Nenhuma ocorrência ainda.</p>
          ) : (
            <ul className="divide-y divide-line/60">
              {occurrences.data?.map((occurrence) => (
                <OccurrenceRow
                  key={`${occurrence.routine_id}-${occurrence.start}`}
                  occurrence={occurrence}
                  routineId={routine.id}
                  timezone={timezone}
                  onChanged={() => {
                    void occurrences.refetch();
                    void adherence.refetch();
                  }}
                />
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </Panel>
  );
}

// ---------------------------------------------------------------
const STATUS_STYLE: Record<
  Occurrence["status"],
  { label: string; tone: "neutral" | "ok" | "brand" }
> = {
  planned: { label: "planejada", tone: "neutral" },
  done: { label: "feita", tone: "ok" },
  // Pulada em tom neutro, não em vermelho: §5.9.4 mostra o fato, e
  // pintar a falta de vermelho é a repreensão que o produto evita.
  skipped: { label: "pulada", tone: "neutral" },
  moved: { label: "movida", tone: "brand" },
};

function OccurrenceRow({
  occurrence,
  routineId,
  timezone,
  onChanged,
}: {
  occurrence: Occurrence;
  routineId: string;
  timezone: string;
  onChanged: () => void;
}) {
  const past = new Date(occurrence.end) < new Date();
  const style = STATUS_STYLE[occurrence.status];

  const mark = useMutation({
    mutationFn: (status: "done" | "skipped") =>
      api.post(`/routines/${routineId}/mark`, {
        scheduled_start: occurrence.start,
        status,
      }),
    onSuccess: onChanged,
  });

  return (
    <li className="flex flex-wrap items-center gap-2 py-2 text-xs">
      <span className="tnum min-w-0 flex-1 truncate text-muted">
        {range(occurrence.start, occurrence.end, timezone)}
      </span>

      <Badge tone={style.tone}>{style.label}</Badge>

      {occurrence.actual_min ? (
        <span className="tnum text-muted">{duration(occurrence.actual_min)}</span>
      ) : null}

      {occurrence.moved_to ? (
        <span className="text-brand">→ {moment(occurrence.moved_to, timezone)}</span>
      ) : null}

      {/* Marcação rápida (§8.1): só para o que já passou. Oferecer
          "feito" numa ocorrência de amanhã convidaria a registrar o que
          não aconteceu, e aí o relatório mede ficção. */}
      {past && occurrence.status === "planned" ? (
        <span className="flex gap-1">
          <button
            onClick={() => mark.mutate("done")}
            disabled={mark.isPending}
            aria-label="Marcar como feita"
            className="flex items-center gap-1 rounded-md px-2 py-1 font-medium text-ok transition-colors hover:bg-ok/12 disabled:opacity-40"
          >
            <IconCheck className="h-3 w-3" />
            feito
          </button>
          <button
            onClick={() => mark.mutate("skipped")}
            disabled={mark.isPending}
            aria-label="Marcar como pulada"
            className="flex items-center gap-1 rounded-md px-2 py-1 text-muted transition-colors hover:bg-surface-3 hover:text-ink disabled:opacity-40"
          >
            <IconSkip className="h-3 w-3" />
            pulei
          </button>
        </span>
      ) : null}
    </li>
  );
}

/**
 * RRULE em português, para os casos comuns.
 *
 * Não é um parser de RFC 5545 — a expansão de verdade acontece no
 * backend, com `python-dateutil` (§5.9.4), e reimplementá-la aqui seria
 * duplicar a parte difícil no lugar errado. Isto só lê `FREQ` e `BYDAY`
 * para a linha de resumo, e cai no texto cru quando não reconhece.
 */
const WEEKDAY: Record<string, string> = {
  MO: "seg",
  TU: "ter",
  WE: "qua",
  TH: "qui",
  FR: "sex",
  SA: "sáb",
  SU: "dom",
};

function describeRule(rrule: string): string {
  const parts = new Map(
    rrule
      .replace(/^RRULE:/i, "")
      .split(";")
      .map((piece) => piece.split("=") as [string, string]),
  );

  const freq = parts.get("FREQ");
  const byDay = parts.get("BYDAY");

  if (freq === "WEEKLY" && byDay) {
    const days = byDay
      .split(",")
      .map((day) => WEEKDAY[day.replace(/^[+-]?\d/, "")] ?? day)
      .join("/");
    return `toda semana · ${days}`;
  }
  if (freq === "DAILY") return "todo dia";
  if (freq === "WEEKLY") return "toda semana";
  if (freq === "MONTHLY") return "todo mês";
  return rrule;
}
