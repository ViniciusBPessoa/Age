/**
 * Timeline da agenda (§8.1).
 *
 * "Visão semana/mês. Eventos criados pelo Age marcados visualmente.
 * Cadeado para fixar/soltar flexibilidade. Diferencia proposto
 * (hachurado) de confirmado."
 *
 * As quatro coisas estão aqui. O cadeado é o detalhe que mais importa
 * do ponto de vista do domínio: ele deixa a pessoa corrigir a
 * heurística de classificação (§5.6), que erra por desenho — ela tem
 * viés para rígido justamente porque mover algo que não podia ser
 * movido é pior que deixar de mover algo que podia.
 */

"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addMonths,
  addWeeks,
  endOfMonth,
  endOfWeek,
  startOfMonth,
  startOfWeek,
} from "date-fns";
import { ptBR } from "date-fns/locale";
import { useMemo, useState } from "react";

import {
  CalendarTimeline,
  type Proposal,
  type TimelineView,
} from "@/components/calendar-timeline/calendar-timeline";
import { Badge, Button, Dialog, LoadError, Select } from "@/components/ui";
import { api } from "@/lib/api-client";
import { range } from "@/lib/format";
import type { AgeEvent, Flexibility, Plan } from "@/lib/types";

const FLEX_LABEL: Record<Flexibility, string> = {
  fixed: "Fixo — não movo",
  semi_flexible: "Semi-flexível — movo avisando",
  flexible: "Flexível — movo quando precisar",
};

export function CalendarScreen({ timezone }: { timezone: string }) {
  const queryClient = useQueryClient();
  const [view, setView] = useState<TimelineView>("week");
  const [cursor, setCursor] = useState(() => new Date());
  const [selected, setSelected] = useState<AgeEvent | null>(null);

  // A janela buscada é maior que a visível: `react-big-calendar` mostra
  // dias da semana anterior e seguinte nas bordas do mês, e buscar só o
  // mês exato deixaria esses dias vazios na tela.
  const window = useMemo(() => {
    const base =
      view === "month"
        ? { from: startOfMonth(cursor), to: endOfMonth(cursor) }
        : { from: cursor, to: cursor };
    return {
      start: startOfWeek(base.from, { locale: ptBR }).toISOString(),
      end: endOfWeek(base.to, { locale: ptBR }).toISOString(),
    };
  }, [cursor, view]);

  const events = useQuery({
    queryKey: ["events", window.start, window.end],
    queryFn: () =>
      api.get<{ events: AgeEvent[] }>("/calendar/events", {
        start: window.start,
        end: window.end,
      }),
  });

  /**
   * Planos ainda propostos viram eventos hachurados na timeline (§8.1).
   *
   * Mostrar a proposta junto do confirmado é o que permite julgar se
   * ela cabe: um cartão de diff diz "qua 14:00–15:30", mas só a
   * timeline mostra que isso fica encostado na aula das 16h.
   */
  const plans = useQuery({
    queryKey: ["plans", "proposed"],
    queryFn: () => api.get<Plan[]>("/plans", { status: "proposed" }),
  });

  const proposals = useMemo<Proposal[]>(() => {
    const out: Proposal[] = [];
    for (const plan of plans.data ?? []) {
      for (const item of plan.items) {
        if (item.status !== "pending" || item.target !== "event") continue;
        const after = item.after as {
          start?: string;
          end?: string;
          title?: string;
        } | null;
        if (!after?.start || !after.end) continue;
        out.push({
          id: item.id,
          title: after.title ?? "(proposto)",
          start: after.start,
          end: after.end,
        });
      }
    }
    return out;
  }, [plans.data]);

  const byId = useMemo(() => {
    const map = new Map<string, AgeEvent>();
    for (const event of events.data?.events ?? []) map.set(event.id, event);
    return map;
  }, [events.data]);

  async function setFlexibility(eventId: string, flexibility: Flexibility) {
    await api.patch<AgeEvent>(`/calendar/events/${eventId}/flexibility`, {
      flexibility,
    });
    await queryClient.invalidateQueries({ queryKey: ["events"] });
    setSelected(null);
  }

  async function remove(eventId: string) {
    await api.del(`/calendar/events/${eventId}`);
    await queryClient.invalidateQueries({ queryKey: ["events"] });
    setSelected(null);
  }

  function shift(direction: -1 | 1) {
    setCursor((current) =>
      view === "month"
        ? addMonths(current, direction)
        : addWeeks(current, view === "day" ? direction / 7 : direction),
    );
  }

  return (
    <div className="flex h-dvh flex-col pb-14 md:pb-0">
      <header className="flex flex-wrap items-center gap-2 border-b border-[var(--border)] px-4 py-3">
        <h1 className="text-sm font-semibold">Agenda</h1>
        {proposals.length > 0 ? (
          <Badge tone="proposed">
            {proposals.length} proposto{proposals.length === 1 ? "" : "s"}
          </Badge>
        ) : null}
        <div className="ml-auto flex items-center gap-1">
          <Button
            size="sm"
            variant="quiet"
            onClick={() => shift(-1)}
            aria-label="Anterior"
          >
            ‹
          </Button>
          <Button size="sm" variant="quiet" onClick={() => setCursor(new Date())}>
            hoje
          </Button>
          <Button
            size="sm"
            variant="quiet"
            onClick={() => shift(1)}
            aria-label="Próximo"
          >
            ›
          </Button>
        </div>
      </header>

      <div className="min-h-0 flex-1 px-2 py-2 md:px-4 md:py-3">
        {events.isError ? (
          <LoadError
            message="Não consegui carregar sua agenda."
            onRetry={() => void events.refetch()}
          />
        ) : (
          <CalendarTimeline
            events={events.data?.events ?? []}
            proposals={proposals}
            timezone={timezone}
            view={view}
            date={cursor}
            onView={setView}
            onNavigate={setCursor}
            onSelectEvent={(id, kind) => {
              if (kind === "proposal") return;
              setSelected(byId.get(id) ?? null);
            }}
          />
        )}
      </div>

      <Dialog
        open={selected !== null}
        onClose={() => setSelected(null)}
        title={selected?.title ?? ""}
        footer={
          selected ? (
            <>
              <Button
                variant="danger"
                size="sm"
                onClick={() => void remove(selected.id)}
              >
                Apagar
              </Button>
              <Button variant="quiet" size="sm" onClick={() => setSelected(null)}>
                Fechar
              </Button>
            </>
          ) : null
        }
      >
        {selected ? (
          <div className="space-y-4">
            <p className="text-xs text-[var(--muted)]">
              {range(selected.start, selected.end, timezone)}
            </p>

            {selected.location ? (
              <p className="text-xs text-[var(--muted)]">📍 {selected.location}</p>
            ) : null}

            {selected.created_by_age ? (
              <Badge tone="accent">✦ criado pelo Age</Badge>
            ) : null}

            {/* O cadeado de §8.1. Fica num `<Select>` em vez de num
                ícone de duas posições porque são TRÊS estados (§5.6), e
                um cadeado aberto/fechado não representa o meio. */}
            <div className="space-y-1.5">
              <label
                htmlFor="flex"
                className="block text-xs font-medium text-[var(--text)]"
              >
                🔒 O Age pode mover este evento?
              </label>
              <Select
                id="flex"
                value={selected.flexibility}
                onChange={(e) =>
                  void setFlexibility(selected.id, e.target.value as Flexibility)
                }
              >
                {(Object.keys(FLEX_LABEL) as Flexibility[]).map((value) => (
                  <option key={value} value={value}>
                    {FLEX_LABEL[value]}
                  </option>
                ))}
              </Select>
              <p className="text-[11px] leading-relaxed text-[var(--muted)]">
                A classificação automática tende para o rígido de propósito: mover algo
                que não podia ser movido é pior que deixar de mover algo que podia.
              </p>
            </div>
          </div>
        ) : null}
      </Dialog>

      <Legend />
    </div>
  );
}

/**
 * A legenda não é enfeite: sem ela, a diferença entre hachurado e
 * cheio, ou entre borda laranja e azul, é adivinhação. §8.2 pede
 * contraste AA, e cor sozinha nunca é informação suficiente —
 * a legenda é o que torna as cores legíveis para quem não as distingue.
 */
function Legend() {
  const items = [
    { className: "age-ev age-ev-flexible", label: "flexível" },
    { className: "age-ev age-ev-semi", label: "semi-flexível" },
    { className: "age-ev age-ev-fixed", label: "fixo 🔒" },
    { className: "age-ev age-ev-proposed age-hatch", label: "proposto" },
  ];
  return (
    <div className="age-timeline flex flex-wrap items-center gap-3 border-t border-[var(--border)] px-4 py-2 text-[11px] text-[var(--muted)]">
      {items.map((item) => (
        <span key={item.label} className="flex items-center gap-1.5">
          <span aria-hidden className={`${item.className} h-3 w-5 rounded`} />
          {item.label}
        </span>
      ))}
    </div>
  );
}
