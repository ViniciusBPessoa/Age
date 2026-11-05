/**
 * O mês em miniatura da barra lateral, como no Google Agenda.
 *
 * Serve para pular para qualquer dia sem clicar "próximo" cinco vezes, e
 * mostra com um ponto os dias que já têm alguma coisa: dá para ver a
 * semana carregada antes de abrir a semana.
 */

"use client";

import { useQuery } from "@tanstack/react-query";
import {
  addDays,
  addMonths,
  endOfMonth,
  endOfWeek,
  format,
  isSameDay,
  isSameMonth,
  startOfMonth,
  startOfWeek,
} from "date-fns";
import { ptBR } from "date-fns/locale";
import { TZDate } from "@date-fns/tz";
import { useMemo, useState } from "react";

import { Button, IconChevronLeft, IconChevronRight, cx } from "@/components/ui";
import { api } from "@/lib/api-client";
import type { AgeEvent } from "@/lib/types";

const SEMANA = ["D", "S", "T", "Q", "Q", "S", "S"];

export function MiniMes({
  selecionado,
  onEscolher,
  timezone,
}: {
  selecionado: Date;
  onEscolher: (dia: Date) => void;
  timezone: string;
}) {
  const [mes, setMes] = useState(() => startOfMonth(selecionado));
  // Quando a grade navega para outro mês, a miniatura acompanha.
  const [ultimoSelecionado, setUltimo] = useState(selecionado);
  if (!isSameDay(ultimoSelecionado, selecionado)) {
    setUltimo(selecionado);
    if (!isSameMonth(mes, selecionado)) setMes(startOfMonth(selecionado));
  }

  const inicio = startOfWeek(startOfMonth(mes), { locale: ptBR });
  const fim = endOfWeek(endOfMonth(mes), { locale: ptBR });

  const eventos = useQuery({
    queryKey: ["events", inicio.toISOString(), fim.toISOString()],
    queryFn: () =>
      api.get<{ events: AgeEvent[] }>("/calendar/events", {
        start: inicio.toISOString(),
        end: fim.toISOString(),
      }),
    staleTime: 30_000,
  });

  const diasComEvento = useMemo(() => {
    const set = new Set<string>();
    for (const ev of eventos.data?.events ?? []) {
      set.add(format(new TZDate(new Date(ev.start), timezone), "yyyy-MM-dd"));
    }
    return set;
  }, [eventos.data, timezone]);

  const dias: Date[] = [];
  for (let d = inicio; d <= fim; d = addDays(d, 1)) dias.push(d);
  const hoje = new Date();

  return (
    <div className="select-none">
      <div className="mb-1.5 flex items-center justify-between">
        <span className="pl-1 text-sm font-semibold first-letter:uppercase">
          {format(mes, "MMMM yyyy", { locale: ptBR })}
        </span>
        <div className="flex">
          <Button
            size="icon"
            variant="ghost"
            aria-label="Mês anterior"
            onClick={() => setMes((m) => addMonths(m, -1))}
          >
            <IconChevronLeft className="h-4 w-4" />
          </Button>
          <Button
            size="icon"
            variant="ghost"
            aria-label="Próximo mês"
            onClick={() => setMes((m) => addMonths(m, 1))}
          >
            <IconChevronRight className="h-4 w-4" />
          </Button>
        </div>
      </div>
      <div
        className="grid grid-cols-7 text-center text-2xs font-semibold text-faint"
        aria-hidden
      >
        {SEMANA.map((d, i) => (
          <span key={i} className="py-1">
            {d}
          </span>
        ))}
      </div>
      <div
        className="grid grid-cols-7 gap-y-0.5 text-center"
        role="grid"
        aria-label="Escolher dia"
      >
        {dias.map((d) => {
          const chave = format(d, "yyyy-MM-dd");
          const ehHoje = isSameDay(d, hoje);
          const ehSelecionado = isSameDay(d, selecionado);
          const foraDoMes = !isSameMonth(d, mes);
          return (
            <button
              key={chave}
              type="button"
              onClick={() => onEscolher(d)}
              aria-label={format(d, "EEEE, d 'de' MMMM", { locale: ptBR })}
              aria-current={ehHoje ? "date" : undefined}
              aria-pressed={ehSelecionado}
              className={cx(
                "relative mx-auto grid h-8 w-8 place-items-center rounded-full text-xs tabular-nums transition-colors",
                "focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand",
                ehHoje
                  ? "bg-brand font-bold text-brand-ink"
                  : ehSelecionado
                    ? "bg-brand-soft font-semibold text-brand"
                    : foraDoMes
                      ? "text-faint hover:bg-surface-2"
                      : "text-ink-2 hover:bg-surface-2",
              )}
            >
              {format(d, "d")}
              {diasComEvento.has(chave) && !ehHoje ? (
                <span
                  aria-hidden
                  className="absolute bottom-1 h-1 w-1 rounded-full bg-brand"
                />
              ) : null}
            </button>
          );
        })}
      </div>
    </div>
  );
}
