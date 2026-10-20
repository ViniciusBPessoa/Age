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

import {
  Badge,
  IconChevronRight,
  IconReport,
  LoadError,
  PageHeader,
  Panel,
  Progress,
  Select,
  SkeletonList,
} from "@/components/ui";
import { UsagePanel } from "@/components/report/usage-panel";
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
    <div className="min-h-dvh pb-16 md:pb-0">
      <PageHeader
        title="Relatório"
        subtitle={
          report.data
            ? `${dateOf(report.data.period_start, timezone)} a ${dateOf(
                report.data.period_end,
                timezone,
              )}`
            : undefined
        }
        action={
          <>
            <label htmlFor="range" className="sr-only">
              Período
            </label>
            <Select
              id="range"
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              className="h-9 w-auto text-xs"
            >
              {RANGES.map((option) => (
                <option key={option.days} value={option.days}>
                  {option.label}
                </option>
              ))}
            </Select>
          </>
        }
      />

      <div className="mx-auto max-w-3xl space-y-5 px-4 py-6 md:px-7">
        {report.isError ? (
          <LoadError
            message="Não consegui montar o relatório."
            onRetry={() => void report.refetch()}
          />
        ) : report.isLoading ? (
          <SkeletonList rows={3} />
        ) : report.data ? (
          <>
            {/* A frase do domínio, no tamanho que ela merece: é a
                leitura do período, e vinha em 13px cinza acima de três
                caixas. Sem elogio, por desenho (§5.9.9). */}
            <Panel className="px-5 py-5">
              <p className="whitespace-pre-wrap text-md leading-[1.65] text-ink">
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
                ratio={
                  report.data.study_planned > 0
                    ? report.data.study_done / report.data.study_planned
                    : null
                }
              />
              <Metric
                label="Tempo estudado"
                value={duration(report.data.study_done_minutes)}
                note={`de ${duration(report.data.study_planned_minutes)} planejados`}
                ratio={
                  report.data.study_planned_minutes > 0
                    ? report.data.study_done_minutes / report.data.study_planned_minutes
                    : null
                }
              />
              <Metric
                label="Eventos na agenda"
                value={String(report.data.events_total)}
                note="no período"
                ratio={null}
              />
            </div>

            {/* §5.9.9: o relatório procura o padrão. Em destaque porque
                é a parte acionável — número sozinho descreve, padrão
                sugere o que mudar. */}
            {report.data.patterns.length > 0 ? (
              <Panel className="overflow-hidden">
                <h2 className="eyebrow border-b border-line px-5 py-3">
                  O que chamou atenção
                </h2>
                <ul className="divide-y divide-line">
                  {report.data.patterns.map((pattern) => (
                    <li
                      key={pattern}
                      className="flex items-start gap-2.5 px-5 py-3.5 text-sm leading-relaxed text-ink-2"
                    >
                      <IconChevronRight className="mt-0.5 h-3.5 w-3.5 shrink-0 text-brand" />
                      {pattern}
                    </li>
                  ))}
                </ul>
              </Panel>
            ) : null}

            {report.data.routines.length > 0 ? (
              <section>
                <h2 className="eyebrow px-1 pb-2">Rotinas</h2>
                <ul className="space-y-2">
                  {report.data.routines.map((adherence) => (
                    <Panel
                      as="li"
                      key={adherence.routine_id ?? adherence.routine_title}
                      className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-3.5 md:px-5"
                    >
                      <span className="min-w-0 flex-1 truncate text-sm font-medium">
                        {adherence.routine_title}
                      </span>
                      <span className="tnum text-base font-semibold">
                        {adherence.done}
                        <span className="text-sm font-normal text-muted">
                          /{adherence.planned}
                        </span>
                      </span>
                      {adherence.moved > 0 ? (
                        <span className="text-xs text-muted">
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

            {/* Consumo do assistente. Período próprio, porque "como foi
                minha semana de estudo?" e "quanto o Age consumiu?" não
                precisam da mesma janela. */}
            <UsagePanel />
          </>
        ) : null}
      </div>
    </div>
  );
}

/**
 * Um número do período.
 *
 * O valor é 28px: era 18px, e num relatório o número É o conteúdo. A
 * barra só aparece quando a razão faz sentido (sessões feitas sobre
 * planejadas); para "eventos na agenda" não há denominador, e desenhar
 * uma barra cheia ali sugeriria uma meta que não existe.
 */
function Metric({
  label,
  value,
  note,
  ratio,
}: {
  label: string;
  value: string;
  note: string;
  ratio: number | null;
}) {
  return (
    <Panel className="px-4 py-4">
      <p className="eyebrow">{label}</p>
      <p className="tnum mt-2 text-2xl font-semibold leading-none">{value}</p>
      <p className="mt-1.5 text-xs text-muted">{note}</p>
      {ratio !== null ? (
        <Progress
          value={Math.round(ratio * 100)}
          tone={ratio >= 0.8 ? "ok" : ratio >= 0.5 ? "brand" : "warn"}
          label={label}
          className="mt-3"
        />
      ) : null}
    </Panel>
  );
}
