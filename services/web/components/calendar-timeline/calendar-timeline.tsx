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

import { useCallback, useMemo, type CSSProperties } from "react";
import {
  Calendar,
  dateFnsLocalizer,
  type Components,
  type Event as RBCEvent,
} from "react-big-calendar";
import withDragAndDrop, {
  type EventInteractionArgs,
} from "react-big-calendar/lib/addons/dragAndDrop";
import { format, getDay, parse, startOfWeek } from "date-fns";
import { ptBR } from "date-fns/locale";

import { daGrade, paraGrade } from "@/lib/fuso-da-grade";

import { IconLock, IconSparkle } from "@/components/ui/icons";
import type { AgeEvent, EventColor, Flexibility } from "@/lib/types";

import "react-big-calendar/lib/css/react-big-calendar.css";
import "react-big-calendar/lib/addons/dragAndDrop/styles.css";
import "./timeline.css";

const localizer = dateFnsLocalizer({
  format,
  parse,
  startOfWeek: (date: Date) => startOfWeek(date, { locale: ptBR }),
  getDay,
  locales: { "pt-BR": ptBR },
});

/** O `<Calendar>` com arrastar e redimensionar, criado uma vez. */
const DnDCalendar = withDragAndDrop<Entry>(Calendar);

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
  color: EventColor | null;
}

type Entry = RBCEvent & { payload: Payload };

export type TimelineView = "week" | "month" | "day" | "agenda";

/** Onde o clique aconteceu na tela, para ancorar um balão ao lado. */
export interface Ancora {
  x: number;
  y: number;
  largura?: number;
  altura?: number;
}

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
  onCriarNoGrid,
  onSoltarDeFora,
  arrastandoDeFora,
}: {
  events: AgeEvent[];
  proposals?: Proposal[];
  timezone: string;
  view?: TimelineView;
  date: Date;
  onView?: (view: TimelineView) => void;
  onNavigate?: (date: Date) => void;
  onSelectEvent?: (id: string, kind: "event" | "proposal", ancora: Ancora) => void;
  /**
   * Arrastar um bloco para outro horário, ou puxar a borda para mudar a
   * duração. `kind` diz se era evento ou proposta: a tela grava cada um
   * pela rota certa (ver a nota no fim do arquivo). As datas chegam já
   * convertidas para instante (UTC) no fuso da CONTA.
   */
  onEventDrop?: (
    id: string,
    kind: "event" | "proposal",
    start: Date,
    end: Date,
    acao: "mover" | "duracao",
  ) => void;
  /** Uma tarefa da barra lateral solta na grade. */
  onSoltarDeFora?: (inicio: Date, fim: Date) => void;
  /** Duração (min) do que está sendo arrastado de fora, para a prévia. */
  arrastandoDeFora?: { titulo: string; minutos: number } | null;
  /**
   * Arrastar numa faixa VAZIA para criar um compromisso (§8.1).
   *
   * Diferente de `onEventDrop`, e a diferença decide: mover um evento
   * que já existe é mudança na agenda e passa por aprovação (ver a
   * nota no fim do arquivo); criar um novo é ação direta da pessoa,
   * igual ao `POST /calendar/events` que a tela já usa.
   */
  onCriarNoGrid?: (inicio: Date, fim: Date, ancora: Ancora | null) => void;
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
  // As duas conversões de fuso moram em `lib/fuso-da-grade.ts`, com
  // teste: foi ali que a grade desenhava tudo 3h adiantado.
  const toLocal = useCallback((iso: string) => paraGrade(iso, timezone), [timezone]);
  const toAccount = useCallback((d: Date) => daGrade(d, timezone), [timezone]);

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
        color: event.color ?? null,
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
        color: null,
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
    // A prévia de uma tarefa arrastada de fora chega aqui sem `payload`.
    if (!entry.payload) return { className: "age-ev age-hatch age-ev-proposed" };
    const { kind, flexibility, createdByAge } = entry.payload;
    const classes = ["age-ev"];
    if (kind === "proposal") classes.push("age-hatch", "age-ev-proposed");
    else if (flexibility === "fixed") classes.push("age-ev-fixed");
    else if (flexibility === "semi_flexible") classes.push("age-ev-semi");
    else classes.push("age-ev-flexible");
    if (createdByAge && kind === "event") classes.push("age-ev-by-age");
    /*
     * A cor de categoria pinta o bloco; o TIPO continua legível pela
     * forma: cadeado no fixo, hachura no proposto. Cor sozinha nunca é
     * a única pista (§8.2).
     */
    const color = entry.payload.color;
    if (color && kind === "event") {
      classes.push("age-ev-colorida");
      return {
        className: classes.join(" "),
        style: { "--ev": `var(--ev-${color})` } as CSSProperties,
      };
    }
    return { className: classes.join(" ") };
  }, []);

  const soltar = useCallback(
    (acao: "mover" | "duracao") =>
      ({ event, start, end }: EventInteractionArgs<Entry>) => {
        onEventDrop?.(
          event.payload.id,
          event.payload.kind,
          toAccount(new Date(start)),
          toAccount(new Date(end)),
          acao,
        );
      },
    [onEventDrop, toAccount],
  );

  const components = useMemo<Components<Entry>>(
    () => ({
      event: ({ event }) =>
        !event.payload ? (
          <span className="truncate">{event.title as string}</span>
        ) : (
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
                aria-label="fixo: o Age não move"
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
      /*
       * Cabeçalho do dia como no Google Agenda: o dia da semana pequeno
       * em cima, o número grande embaixo, e hoje numa pílula da cor da
       * marca. O olho acha "hoje" antes de ler qualquer data.
       */
      week: {
        header: ({ date }: { date: Date }) => <CabecalhoDoDia date={date} />,
      },
      day: {
        header: ({ date }: { date: Date }) => <CabecalhoDoDia date={date} />,
      },
    }),
    [],
  );

  return (
    <div className="age-timeline h-full">
      <DnDCalendar
        localizer={localizer}
        culture="pt-BR"
        events={entries}
        view={view}
        date={date}
        views={["day", "week", "month", "agenda"]}
        resizable
        showMultiDayTimes
        onView={(next) => onView?.(next as TimelineView)}
        onNavigate={(next) => onNavigate?.(next)}
        onSelectEvent={(entry, e) => {
          if (!entry.payload) return;
          const alvo = (e?.currentTarget ?? e?.target) as HTMLElement | undefined;
          const r = alvo?.getBoundingClientRect?.();
          onSelectEvent?.(
            entry.payload.id,
            entry.payload.kind,
            r
              ? { x: r.left, y: r.top, largura: r.width, altura: r.height }
              : { x: globalThis.innerWidth / 2, y: globalThis.innerHeight / 3 },
          );
        }}
        /*
         * Arrastar e redimensionar (§8.0): mover um bloco que VOCÊ está
         * olhando é a mesma decisão que mudar a hora no diálogo de
         * edição, que já grava direto. Proposta também arrasta: vira a
         * remarcação que o painel de propostas fazia por formulário.
         * Evento recorrente não arrasta: mover "só esta" ou "todas" é
         * uma pergunta que a grade não sabe fazer.
         */
        draggableAccessor={(entry: Entry) =>
          Boolean(onEventDrop) && entry.payload?.routineId === null
        }
        resizableAccessor={(entry: Entry) =>
          Boolean(onEventDrop) && entry.payload?.routineId === null && !entry.allDay
        }
        onEventDrop={soltar("mover")}
        onEventResize={soltar("duracao")}
        onDropFromOutside={({ start, end, allDay }) => {
          if (!onSoltarDeFora || allDay) return;
          const inicio = toAccount(new Date(start));
          const minutos = arrastandoDeFora?.minutos ?? 60;
          const fim =
            arrastandoDeFora === null || arrastandoDeFora === undefined
              ? toAccount(new Date(end))
              : new Date(inicio.getTime() + minutos * 60_000);
          onSoltarDeFora(inicio, fim);
        }}
        dragFromOutsideItem={
          arrastandoDeFora
            ? () =>
                ({
                  title: arrastandoDeFora.titulo,
                  start: new Date(),
                  end: new Date(Date.now() + arrastandoDeFora.minutos * 60_000),
                }) as never
            : undefined
        }
        /*
         * Arrastar numa faixa vazia cria um compromisso.
         *
         * `selectable` é da lib e já trata ponteiro e toque, então não
         * reimplemento `pointerdown/move/up` à mão.
         *
         * Um CLIQUE seco também chega aqui, com `start === end` ou uma
         * faixa de um slot. Sem o mínimo abaixo, ele viraria um bloco
         * de 0 ou 30 minutos que ninguém pediu — por isso a duração é
         * elevada a 1h, que é o que a pessoa quase sempre quer e pode
         * ajustar no diálogo que abre em seguida.
         */
        selectable={onCriarNoGrid ? true : false}
        onSelectSlot={(slot) => {
          if (!onCriarNoGrid) return;
          const inicio = new Date(slot.start);
          const fim = new Date(slot.end);
          const minimo = new Date(inicio.getTime() + 60 * 60_000);
          // `box` vem do clique, `bounds` do arrasto: os dois dizem onde
          // ancorar o balão de criação rápida, ao lado do que foi marcado.
          const caixa = (slot as { box?: { x: number; y: number } }).box;
          const limites = (
            slot as {
              bounds?: { left: number; top: number; right: number; bottom: number };
            }
          ).bounds;
          const ancora: Ancora | null = limites
            ? {
                x: limites.left,
                y: limites.top,
                largura: limites.right - limites.left,
                altura: limites.bottom - limites.top,
              }
            : caixa
              ? { x: caixa.x, y: caixa.y }
              : null;
          // No mês, o clique é num DIA: sem hora, o padrão é 9h às 10h.
          const semHora =
            view === "month" ||
            (slot.slots.length === 1 &&
              inicio.getHours() === 0 &&
              fim.getHours() === 0);
          if (semHora && view === "month") {
            const nove = new Date(inicio);
            nove.setHours(9, 0, 0, 0);
            onCriarNoGrid(
              toAccount(nove),
              toAccount(new Date(nove.getTime() + 60 * 60_000)),
              ancora,
            );
            return;
          }
          onCriarNoGrid(
            toAccount(inicio),
            toAccount(fim.getTime() - inicio.getTime() < 60 * 60_000 ? minimo : fim),
            ancora,
          );
        }}
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
        // Sem isto a lib rola sozinha para perto da hora atual e para
        // alguns pixels adiante do começo do primeiro bloco: o
        // "08:00 – 10:00" das aulas ficava com a primeira linha cortada
        // pela borda do cabeçalho. Ancorar às 7h deixa a manhã inteira
        // visível de cara, com uma hora de folga acima.
        scrollToTime={new Date(1970, 0, 1, 7, 0)}
        formats={{
          agendaDateFormat: (d: Date) => format(d, "EEE, d 'de' MMM", { locale: ptBR }),
          agendaTimeRangeFormat: ({ start, end }: { start: Date; end: Date }) =>
            `${format(start, "HH:mm")} – ${format(end, "HH:mm")}`,
          agendaHeaderFormat: ({ start, end }: { start: Date; end: Date }) =>
            `${format(start, "d MMM", { locale: ptBR })} – ${format(end, "d MMM", { locale: ptBR })}`,
          eventTimeRangeFormat: ({ start, end }: { start: Date; end: Date }) =>
            `${format(start, "HH:mm")} – ${format(end, "HH:mm")}`,
          timeGutterFormat: (d: Date) => format(d, "HH:mm"),
        }}
        length={30}
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
          allDay: "dia todo",
          noEventsInRange: "Nada marcado neste período.",
          showMore: (count: number) => `+${count}`,
        }}
      />
    </div>
  );
}

// O `EEE` do ptBR do date-fns devolve o nome inteiro ("domingo"), que
// não cabe em sete colunas. A abreviação de três letras é a do Google.
const DIAS_CURTOS = ["dom", "seg", "ter", "qua", "qui", "sex", "sáb"];

function CabecalhoDoDia({ date }: { date: Date }) {
  const hoje = new Date();
  const ehHoje =
    date.getFullYear() === hoje.getFullYear() &&
    date.getMonth() === hoje.getMonth() &&
    date.getDate() === hoje.getDate();
  return (
    <span className="age-cab-dia" data-hoje={ehHoje || undefined}>
      <span className="age-cab-dia-semana">{DIAS_CURTOS[date.getDay()]}</span>
      <span className="age-cab-dia-numero">{format(date, "d")}</span>
    </span>
  );
}

/*
 * Sobre `onEventDrop`: ligado, e por qual rota.
 *
 * A versão anterior deixava o arrastar desligado porque mover um evento
 * seria "mudança na agenda" que deveria passar por proposta. §8.0
 * corrigiu essa leitura para a edição direta: a regra de aprovação é
 * sobre o que o MODELO propõe. Mover um bloco com o mouse é a mesma
 * decisão que trocar a hora no diálogo de edição, que já grava por
 * `PATCH /calendar/events/{id}`. A tela oferece "Desfazer" logo depois,
 * que devolve o horário anterior pela mesma rota.
 *
 * Proposta arrastada vai por `PATCH /plans/items/{id}`, a remarcação que
 * o painel de propostas já fazia por formulário: continua proposta, e
 * continua precisando de aprovação.
 */
