/**
 * Relatório do período (§5.9.9).
 *
 * "Números reais, sem elogio vazio." A regra do documento é literal:
 * "parabéns" com 2 de 6 sessões feitas destrói a confiança em todo o
 * resto — se o produto elogia um resultado ruim, a pessoa para de
 * acreditar nos números bons também.
 *
 * Por isso a frase do topo é `summary_text`, vinda do backend, e não
 * montada aqui. O tom é decisão de domínio e está testado lá; remontar
 * a frase no componente reabriria a decisão num lugar onde ninguém a
 * verifica. A tela só organiza os números em volta dela.
 *
 * Os padrões (`patterns`) também vêm prontos: §5.9.9 quer que o
 * relatório **ache** o padrão ("as sessões de sexta 19h você pulou 3 de
 * 4 vezes"), e isso é busca estatística com limiar — 3 amostras
 * mínimas, 60% — não formatação.
 */

"use client";

import { useQuery } from "@tanstack/react-query";
import { subDays } from "date-fns";
import { useMemo, useState } from "react";

import { Badge, LoadError, Panel, Select, Spinner } from "@/components/ui";
import { api } from "@/lib/api-client";
import { dateOf, duration, percent } from "@/lib/format";
import type { PeriodReport } from "@/lib/types";

const RANGES = [
  { days: 7, label: "últimos 7 dias" },
  { days: 14, label: "últimas 2 semanas" },
  { days: 30, label: "últimos 30 dias" },
] as const;

export function ReportScreen({ timezone }: { timezone: string }) {
  const [days, setDays] = useState<number>(7);

  const window = useMemo(() => {
    const end = new Date();
    return { start: subDays(end, days).toISOString(), end: end.toISOString() };
  }, [days]);

  const report = useQuery({
    queryKey: ["report", days],
    queryFn: () =>
      api.get<PeriodReport>("/reports/period", {
        start: window.start,
        end: window.end,
      }),
  });

  return (
    <div className="min-h-dvh pb-14 md:pb-0">
      <header className="flex flex-wrap items-center gap-2 border-b border-[var(--border)] px-4 py-3">
        <h1 className="text-sm font-semibold">Relatório</h1>
        <label htmlFor="range" className="sr-only">
          Período
        </label>
        <Select
          id="range"
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          className="ml-auto w-auto py-1 text-xs"
        >
          {RANGES.map((option) => (
            <option key={option.days} value={option.days}>
              {option.label}
            </option>
          ))}
        </Select>
      </header>

      <div className="mx-auto max-w-3xl space-y-4 px-4 py-5">
        {report.isError ? (
          <LoadError
            message="Não consegui montar o relatório."
            onRetry={() => void report.refetch()}
          />
        ) : report.isLoading ? (
          <p className="px-2 py-6 text-xs text-[var(--muted)]">
            <Spinner /> levantando os números…
          </p>
        ) : report.data ? (
          <>
            <Panel className="px-4 py-4">
              <p className="text-[11px] text-[var(--muted)]">
                {dateOf(report.data.period_start, timezone)} a{" "}
                {dateOf(report.data.period_end, timezone)}
              </p>
              {/* A frase do domínio. Sem elogio por desenho. */}
              <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed">
                {report.data.summary_text}
              </p>
            </Panel>

            <div className="grid gap-3 sm:grid-cols-3">
              <Metric
                label="Sessões de estudo"
                value={`${report.data.study_done}/${report.data.study_planned}`}
                note={
                  report.data.study_planned > 0
                    ? percent(report.data.study_completion)
                    : "nada planejado"
                }
              />
              <Metric
                label="Tempo estudado"
                value={duration(report.data.study_done_minutes)}
                note={`de ${duration(report.data.study_planned_minutes)} planejados`}
              />
              <Metric
                label="Eventos na agenda"
                value={String(report.data.events_total)}
                note="no período"
              />
            </div>

            {/* §5.9.9: o relatório procura o padrão. Em destaque porque
                é a parte acionável — número sozinho descreve, padrão
                sugere o que mudar. */}
            {report.data.patterns.length > 0 ? (
              <Panel className="px-4 py-3">
                <h2 className="text-xs font-semibold">O que chamou atenção</h2>
                <ul className="mt-2 space-y-1.5">
                  {report.data.patterns.map((pattern) => (
                    <li key={pattern} className="text-sm leading-relaxed">
                      <span aria-hidden className="mr-1.5 text-[var(--accent)]">
                        →
                      </span>
                      {pattern}
                    </li>
                  ))}
                </ul>
              </Panel>
            ) : null}

            {report.data.routines.length > 0 ? (
              <section>
                <h2 className="px-1 pb-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--muted)]">
                  Rotinas
                </h2>
                <ul className="space-y-2">
                  {report.data.routines.map((adherence) => (
                    <Panel
                      as="li"
                      key={adherence.routine_id ?? adherence.routine_title}
                      className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-sm"
                    >
                      <span className="min-w-0 flex-1 truncate">
                        {adherence.routine_title}
                      </span>
                      <span>
                        <strong>
                          {adherence.done}/{adherence.planned}
                        </strong>
                      </span>
                      {adherence.moved > 0 ? (
                        <span className="text-xs text-[var(--muted)]">
                          {adherence.moved} movida
                          {adherence.moved === 1 ? "" : "s"}
                        </span>
                      ) : null}
                      {/* `meets_target` pode ser `null` (sem meta), e
                          `null` não é "não bateu" — é "não havia meta".
                          Tratar os dois igual acusaria falta onde não há
                          compromisso. */}
                      {adherence.meets_target === true ? (
                        <Badge tone="ok">na meta</Badge>
                      ) : adherence.meets_target === false ? (
                        <Badge tone="neutral">
                          {adherence.per_week.toFixed(1)}x de{" "}
                          {adherence.target_per_week}x
                        </Badge>
                      ) : null}
                    </Panel>
                  ))}
                </ul>
              </section>
            ) : null}
          </>
        ) : null}
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  note,
}: {
  label: string;
  value: string;
  note: string;
}) {
  return (
    <Panel className="px-4 py-3">
      <p className="text-[10px] uppercase tracking-wide text-[var(--muted)]">{label}</p>
      <p className="mt-1 text-lg font-semibold">{value}</p>
      <p className="text-[11px] text-[var(--muted)]">{note}</p>
    </Panel>
  );
}
