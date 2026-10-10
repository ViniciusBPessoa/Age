/**
 * `<CalendarTimeline>` — a biblioteca de calendário fica aqui dentro.
 *
 * ARQUITETURA.md §2.4: "A decisão fica isolada num componente
 * `<CalendarTimeline>` com props próprias do domínio (`events`,
 * `proposals`, `onEventDrop`), para que trocar a lib não vaze para o
 * resto do front. Mesmo princípio das portas do backend, aplicado na
 * UI."
 *
 * É literalmente uma porta: as props falam `AgeEvent` e `Flexibility`,
 * nunca `RBCEvent` ou `SlotInfo`. Nenhum arquivo fora deste diretório
 * importa `react-big-calendar`, e um teste de arquitetura cobra isso.
 *
 * ## O requisito que podia forçar a troca da lib, e não forçou
 *
 * §2.4 registrou o risco: "O requisito que pode forçar a troca é
 * renderizar evento proposto com estilo distinto do confirmado (§8.1) —
 * se o `eventPropGetter` não der conta, FullCalendar tem controle mais
 * fino."
 *
 * Deu conta. `eventPropGetter` devolve `className`, e a hachura do
 * proposto é uma classe CSS (`.age-hatch`, em globals.css) em vez de
 * estilo inline — que é o que permite usar `repeating-linear-gradient`,
 * impossível de expressar num objeto de estilo com uma propriedade só.
 * `react-big-calendar` fica, e a decisão de §2.4 se mantém.
 *
 * ## A versão também importou
 *
 * O documento não fixa versão, e o `package.json` pedia `1.17.1`, que
 * declara peer `react@^16 || ^17 || ^18` e recusa instalar com React
 * 19. A `1.20.0` aceita. Se nenhuma versão aceitasse, aí sim a escolha
 * de §2.4 teria de ser reaberta — não por causa do estilo do proposto,
 * mas por compatibilidade.
 */

"use client";

import { useCallback, useMemo } from "react";
import {
  Calendar,
  dateFnsLocalizer,
  type Components,
  type Event as RBCEvent,
} from "react-big-calendar";
import { format, getDay, parse, startOfWeek } from "date-fns";
import { ptBR } from "date-fns/locale";

import { TZDate } from "@date-fns/tz";

import { IconLock, IconSparkle } from "@/components/ui/icons";
import type { AgeEvent, Flexibility } from "@/lib/types";

import "react-big-calendar/lib/css/react-big-calendar.css";
import "./timeline.css";

const localizer = dateFnsLocalizer({
  format,
  parse,
  startOfWeek: (date: Date) => startOfWeek(date, { locale: ptBR }),
  getDay,
  locales: { "pt-BR": ptBR },
});

/** Um evento ainda não aplicado, vindo de um `PlanDiff` (§5.7). */
export interface Proposal {
  id: string;
  title: string;
  start: string;
  end: string;
}

interface Payload {
  kind: "event" | "proposal";
  flexibility: Flexibility | null;
  createdByAge: boolean;
  routineId: string | null;
  id: string;
}

type Entry = RBCEvent & { payload: Payload };

export type TimelineView = "week" | "month" | "day";

export function CalendarTimeline({
  events,
  proposals = [],
  timezone,
  view = "week",
  date,
  onView,
  onNavigate,
  onSelectEvent,
  onEventDrop,
}: {
  events: AgeEvent[];
  proposals?: Proposal[];
  timezone: string;
  view?: TimelineView;
  date: Date;
  onView?: (view: TimelineView) => void;
  onNavigate?: (date: Date) => void;
  onSelectEvent?: (id: string, kind: "event" | "proposal") => void;
  /** Arrastar no grid. Ainda não ligado — ver nota no fim do arquivo. */
  onEventDrop?: (id: string, start: Date, end: Date) => void;
}) {
  /**
   * Converte ISO (UTC) em `Date` no fuso da conta.
   *
   * A lib trabalha com `Date` local do browser. Sem esta conversão, um
   * evento das 14:00 em São Paulo apareceria às 17:00 para quem abrisse
   * de Lisboa — o mesmo bug de fuso que o M5 teve no relatório, e que é
   * o tipo de erro que faz a pessoa concluir que o sistema está errado
   * sobre a agenda dela.
   */
  const toLocal = useCallback(
    (iso: string) => new Date(new TZDate(new Date(iso), timezone).toString()),
    [timezone],
  );

  const entries = useMemo<Entry[]>(() => {
    const confirmed = events.map<Entry>((event) => ({
      title: event.title,
      start: toLocal(event.start),
      end: toLocal(event.end),
      allDay: event.all_day,
      payload: {
        kind: "event",
        flexibility: event.flexibility,
        createdByAge: event.created_by_age,
        routineId: null,
        id: event.id,
      },
    }));

    const pending = proposals.map<Entry>((proposal) => ({
      title: proposal.title,
      start: toLocal(proposal.start),
      end: toLocal(proposal.end),
      payload: {
        kind: "proposal",
        flexibility: null,
        createdByAge: true,
        routineId: null,
        id: proposal.id,
      },
    }));

    return [...confirmed, ...pending];
  }, [events, proposals, toLocal]);

  /**
   * O estilo de cada evento (§8.1).
   *
   *   - **proposto**: hachurado, para não ser confundido com confirmado;
   *   - **FIXED**: borda laranja — é o que não se mexe (§5.6);
   *   - **criado pelo Age**: marca visual, para a pessoa saber o que
   *     entrou por conversa e o que ela mesma pôs.
   */
  const eventPropGetter = useCallback((entry: Entry) => {
    const { kind, flexibility, createdByAge } = entry.payload;
    const classes = ["age-ev"];
    if (kind === "proposal") classes.push("age-hatch", "age-ev-proposed");
    else if (flexibility === "fixed") classes.push("age-ev-fixed");
    else if (flexibility === "semi_flexible") classes.push("age-ev-semi");
    else classes.push("age-ev-flexible");
    if (createdByAge && kind === "event") classes.push("age-ev-by-age");
    return { className: classes.join(" ") };
  }, []);

  const components = useMemo<Components<Entry>>(
    () => ({
      event: ({ event }) => (
        <span className="flex items-center gap-1 truncate">
          {event.payload.createdByAge && event.payload.kind === "event" ? (
            <IconSparkle
              aria-hidden={false}
              aria-label="criado pelo Age"
              className="h-3 w-3 shrink-0 opacity-80"
            />
          ) : null}
          {event.payload.flexibility === "fixed" ? (
            <IconLock
              aria-hidden={false}
              aria-label="fixo — o Age não move"
              className="h-3 w-3 shrink-0 opacity-80"
            />
          ) : null}
          <span className="truncate">{event.title as string}</span>
        </span>
      ),
      /*
       * A barra da lib sai de cena.
       *
       * A tela de agenda já tem a sua própria — com o título do
       * período, os botões de navegação e o seletor de visão — e
       * manter as duas deixava dois conjuntos de controles para a
       * mesma coisa, um deles sem o estilo do produto.
       */
      toolbar: () => null,
    }),
    [],
  );

  return (
    <div className="age-timeline h-full">
      <Calendar<Entry>
        localizer={localizer}
        culture="pt-BR"
        events={entries}
        view={view}
        date={date}
        views={["day", "week", "month"]}
        onView={(next) => onView?.(next as TimelineView)}
        onNavigate={(next) => onNavigate?.(next)}
        onSelectEvent={(entry) => onSelectEvent?.(entry.payload.id, entry.payload.kind)}
        eventPropGetter={eventPropGetter}
        components={components}
        popup
        step={30}
        timeslots={2}
        // Começa às 6h em vez de 0h: doze linhas vazias no topo empurram
        // o dia útil para fora da tela, e a pessoa abre a agenda e vê
        // madrugada.
        min={new Date(1970, 0, 1, 6, 0)}
        max={new Date(1970, 0, 1, 23, 30)}
        messages={{
          today: "hoje",
          previous: "anterior",
          next: "próximo",
          day: "dia",
          week: "semana",
          month: "mês",
          date: "data",
          time: "hora",
          event: "evento",
          noEventsInRange: "Nada neste período.",
          showMore: (count: number) => `+${count}`,
        }}
      />
    </div>
  );
}

/*
 * Sobre `onEventDrop`: a prop existe na assinatura porque §2.4 a nomeia
 * como parte do contrato deste componente, mas o arrastar não está
 * ligado — e isso é deliberado, não esquecimento.
 *
 * Arrastar um evento no grid é uma mudança na agenda, e §5.8 diz que
 * mudança passa por proposta e aprovação. Ligar o drag direto a um
 * `PATCH` criaria um segundo caminho de escrita que não gera
 * `PlanDiff`, não registra motivo e não entra no `plan_undo_log` — e aí
 * o "desfazer" do produto deixaria de cobrir tudo o que a pessoa fez.
 *
 * O caminho certo é o drag gerar um plano de um item só, com o motivo
 * "você arrastou", e abrir a revisão. Isso precisa de uma rota que
 * ainda não existe (`POST /plans` a partir de uma intenção da UI, sem
 * passar pelo LLM), e inventá-la aqui seria decidir desenho de API
 * dentro de um componente de calendário.
 */
