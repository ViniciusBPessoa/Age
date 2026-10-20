/**
 * Consumo de tokens e custo, no Relatório.
 *
 * Mora aqui e não em Configurações de propósito: Configurações é onde
 * se AJUSTA, Relatório é onde se OLHA número. Consumo e custo são
 * número — e a tela já tinha seletor de período e cartões de métrica,
 * que é exatamente a forma deste painel. Pô-lo em Configurações teria
 * feito a sétima seção de uma página que já rola muito.
 *
 * O que fica em Configurações é só a chave de ligar/desligar a
 * exibição do valor em dinheiro.
 *
 * ## Duas honestidades que a tela precisa manter
 *
 * 1. **Custo desconhecido não é zero.** `null` vira "—" e uma nota,
 *    nunca "US$ 0,00": zero afirma gratuidade.
 * 2. **O histórico tem um degrau.** Até 20/10/2026 o custo era gravado
 *    com o preço de pico e sem desconto de cache, então valores
 *    antigos são maiores que o real. Recalcular é impossível (os
 *    tokens de cache de antes não foram guardados), então a tela diz
 *    isso em vez de fingir precisão.
 */

"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { BarChart } from "@/components/charts/bar-chart";
import { CORES_DE_LEGENDA, StackedBar } from "@/components/charts/stacked-bar";
import { LoadError, Panel, SkeletonList } from "@/components/ui";
import { api } from "@/lib/api-client";
import { formatTokens, formatUsd, useMostrarCusto } from "@/lib/usar-custo";
import type { UsageSummary } from "@/lib/types";

/**
 * O passo vem AMARRADO ao período, não como controle separado.
 *
 * 365 barras diárias num painel de 600px dão 1,6px por barra: um
 * segundo seletor só daria à pessoa a chance de pedir algo ilegível.
 */
const PERIODOS = [
  { chave: "7d", rotulo: "7 dias", dias: 7, bucket: "day" },
  { chave: "30d", rotulo: "30 dias", dias: 30, bucket: "day" },
  { chave: "90d", rotulo: "90 dias", dias: 90, bucket: "week" },
  { chave: "12m", rotulo: "12 meses", dias: 365, bucket: "month" },
] as const;

/** A data em que o cálculo de custo passou a considerar faixa horária
 *  e cache. Antes disso o valor gravado é um teto, não a fatura. */
const CORRECAO = new Date("2026-10-20T00:00:00Z");

export function UsagePanel() {
  const [periodo, setPeriodo] = useState<string>("30d");
  const mostrarCusto = useMostrarCusto();

  const escolha = PERIODOS.find((p) => p.chave === periodo) ?? PERIODOS[1];

  const janela = useMemo(() => {
    // Aritmética de milissegundo, não de calendário: é uma janela
    // deslizante ("os últimos N dias"), e quem decide onde o dia começa
    // é o `date_trunc` do Postgres, no fuso da conta (§9.5).
    const fim = new Date();
    const inicio = new Date(fim.getTime() - escolha.dias * 86_400_000);
    return { start: inicio.toISOString(), end: fim.toISOString() };
  }, [escolha.dias]);

  const uso = useQuery({
    queryKey: ["usage", escolha.chave],
    queryFn: () =>
      api.get<UsageSummary>("/usage/summary", {
        start: janela.start,
        end: janela.end,
        bucket: escolha.bucket,
      }),
  });

  const barras = useMemo(() => {
    const dados = uso.data?.buckets ?? [];
    // Rótulo só de vez em quando: com 30 barras, todos os dias viram
    // uma faixa preta ilegível.
    const passo = Math.max(1, Math.ceil(dados.length / 6));
    return dados.map((b, i) => ({
      label: b.day,
      value: b.prompt_tokens + b.completion_tokens,
      tick: i % passo === 0 ? b.day.slice(8) + "/" + b.day.slice(5, 7) : "",
      title: `${b.day}: ${formatTokens(b.prompt_tokens + b.completion_tokens)} tokens`,
    }));
  }, [uso.data]);

  const temHistoricoAntigo =
    uso.data != null && new Date(uso.data.period_start) < CORRECAO;

  return (
    <section>
      <div className="flex flex-wrap items-center justify-between gap-2 px-1 pb-2">
        <h2 className="eyebrow">Uso do assistente</h2>
        {/* Botões com `aria-pressed`, não rádio: um `<label>` em volta
            de `<input>` não propaga clique em jsdom, e quatro opções
            cabem bem como grupo segmentado. */}
        <div
          role="group"
          aria-label="Período do uso"
          className="flex gap-0.5 rounded-lg bg-surface-2 p-0.5"
        >
          {PERIODOS.map((p) => (
            <button
              key={p.chave}
              type="button"
              aria-pressed={p.chave === periodo}
              onClick={() => setPeriodo(p.chave)}
              className={
                p.chave === periodo
                  ? "rounded-md bg-surface px-2.5 py-1 text-xs font-medium text-ink shadow-sm"
                  : "rounded-md px-2.5 py-1 text-xs text-muted hover:text-ink"
              }
            >
              {p.rotulo}
            </button>
          ))}
        </div>
      </div>

      {uso.isError ? (
        <LoadError
          message="Não consegui ler o seu uso."
          onRetry={() => void uso.refetch()}
        />
      ) : uso.isLoading ? (
        <SkeletonList rows={2} />
      ) : uso.data == null ? null : uso.data.turns === 0 ? (
        <Panel className="px-5 py-6">
          <p className="text-sm text-muted">Nenhuma mensagem neste período.</p>
        </Panel>
      ) : (
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-3">
            <Numero
              rotulo="Mensagens"
              valor={String(uso.data.turns)}
              nota="respostas geradas"
            />
            <Numero
              rotulo="Tokens"
              valor={formatTokens(uso.data.total_tokens)}
              nota={`${formatTokens(uso.data.prompt_tokens)} de entrada`}
            />
            {mostrarCusto ? (
              <Numero
                rotulo="Custo estimado"
                valor={
                  uso.data.estimated_cost_usd === null
                    ? "—"
                    : formatUsd(uso.data.estimated_cost_usd)
                }
                nota={
                  uso.data.estimated_cost_usd === null
                    ? "modelo sem preço conhecido"
                    : "no período"
                }
              />
            ) : null}
          </div>

          <Panel className="px-4 py-4">
            <p className="eyebrow pb-3">Tokens por dia</p>
            <BarChart
              bars={barras}
              ariaLabel={`Tokens por dia, de ${uso.data.period_start.slice(
                0,
                10,
              )} a ${uso.data.period_end.slice(0, 10)}. Total ${formatTokens(
                uso.data.total_tokens,
              )}.`}
              caption="Tokens consumidos por dia"
              formatValue={formatTokens}
            />
          </Panel>

          {uso.data.by_model.length > 0 ? (
            <Panel className="px-4 py-4">
              <p className="eyebrow pb-3">Por modelo</p>
              <StackedBar
                slices={uso.data.by_model.map((m) => ({
                  label: m.model,
                  value: m.prompt_tokens + m.completion_tokens,
                }))}
                ariaLabel="Proporção de tokens por modelo"
                className="mb-3"
              />
              <ul className="space-y-1.5">
                {uso.data.by_model.map((m, i) => (
                  <li
                    key={`${m.provider_key}:${m.model}`}
                    className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs"
                  >
                    <span
                      aria-hidden="true"
                      className={`h-2 w-2 shrink-0 rounded-full ${
                        CORES_DE_LEGENDA[i % CORES_DE_LEGENDA.length]
                      }`}
                    />
                    <span className="min-w-0 flex-1 truncate text-ink-2">
                      {m.model}
                    </span>
                    <span className="tnum text-muted">
                      {formatTokens(m.prompt_tokens + m.completion_tokens)}
                    </span>
                    {mostrarCusto && m.estimated_cost_usd !== null ? (
                      <span className="tnum text-faint">
                        {formatUsd(m.estimated_cost_usd)}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            </Panel>
          ) : null}

          {mostrarCusto && temHistoricoAntigo ? (
            <p className="px-1 text-xs leading-relaxed text-faint">
              Custos de antes de 20/10/2026 foram estimados pelo preço mais caro, sem o
              desconto de cache — são um teto, não a fatura.
            </p>
          ) : null}
        </div>
      )}
    </section>
  );
}

function Numero({
  rotulo,
  valor,
  nota,
}: {
  rotulo: string;
  valor: string;
  nota: string;
}) {
  return (
    <Panel className="px-4 py-4">
      <p className="eyebrow">{rotulo}</p>
      <p className="tnum mt-2 text-2xl font-semibold leading-none">{valor}</p>
      <p className="mt-1.5 text-xs text-muted">{nota}</p>
    </Panel>
  );
}
