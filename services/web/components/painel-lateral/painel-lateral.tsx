/**
 * O painel ao lado da conversa: tarefas e agenda sem sair do chat.
 *
 * Pedido direto: "enquanto eu estiver no chat, eu posso ver os to-dos
 * que eu quero ver de lado". A conversa é onde se decide o que fazer,
 * e até aqui decidir exigia trocar de tela para lembrar o que já
 * existia, e trocar de novo para voltar ao assunto.
 *
 * É uma versão ENXUTA das duas telas, e não as telas inteiras num
 * espaço menor. A grade semanal da agenda em 320px é ilegível, e a
 * lista de tarefas completa traz diálogos de reserva e de edição que
 * competiriam com o cartão de aprovação do chat. Aqui fica o que se
 * consulta de relance: o que está aberto, o que vence, o que tem hoje
 * e amanhã. O resto é um clique em "abrir".
 *
 * As consultas usam as MESMAS chaves das telas cheias (`["tasks",
 * false]`, `["events", ...]`, `["plan", "propostos"]`). Isso importa
 * por dois motivos: o cache é compartilhado, e as invalidações que o
 * chat já faz ao aprovar um plano atualizam o painel sem código novo —
 * a pessoa aprova na conversa e vê a tarefa ou o evento aparecer ao
 * lado.
 */

"use client";

import { TZDate } from "@date-fns/tz";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { addDays, differenceInCalendarDays, format, startOfDay } from "date-fns";
import { ptBR } from "date-fns/locale";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { situacaoDoHorario } from "@/components/tasks/task-list";
import { groupTasks } from "@/components/tasks/tasks-screen";
import {
  IconAlert,
  IconCalendar,
  IconCheck,
  IconClock,
  IconPlus,
  IconSparkle,
  IconTasks,
  IconX,
  LoadError,
  SkeletonList,
  cx,
} from "@/components/ui";
import { RequestFailed, api } from "@/lib/api-client";
import { dayOf, daysUntil, dueLabel, duration, inZone, timeOf } from "@/lib/format";
import type { AbaDoPainel } from "@/lib/ultimo-lugar";
import type { AgeEvent, FreeTime, Plan, Task, TaskListRef } from "@/lib/types";

// ---------------------------------------------------------------
// Regras puras, exportadas para teste
// ---------------------------------------------------------------

/**
 * Hoje e amanhã, no fuso da CONTA (§9.5).
 *
 * O começo do dia é calculado em `TZDate`, não em `Date`: no fuso do
 * browser, quem está em viagem veria "hoje" começar na meia-noite
 * errada, e um compromisso às 23h sumiria do painel ou apareceria no
 * dia seguinte.
 */
export function janelaDoPainel(
  timezone: string,
  agora: Date = new Date(),
  dias = 2,
): { start: string; end: string } {
  const inicio = startOfDay(new TZDate(agora, timezone));
  // `new Date(...)` para sair em UTC (`...Z`): o `toISOString` do
  // `TZDate` escreve com o deslocamento (`-03:00`). É o mesmo instante,
  // mas a mesma janela escrita de dois jeitos viraria duas chaves de
  // cache diferentes.
  return {
    start: new Date(inicio).toISOString(),
    end: new Date(addDays(inicio, dias)).toISOString(),
  };
}

/** Um item da agenda do painel: evento aplicado ou proposta pendente. */
export interface ItemDaAgenda {
  id: string;
  titulo: string;
  start: string;
  end: string;
  diaInteiro: boolean;
  local: string | null;
  /** A classe de cor da barra lateral. */
  cor: string;
  proposto: boolean;
}

/**
 * Cor da barra: a cor escolhida pela pessoa ganha da cor do tipo.
 *
 * Mesma precedência da grade (`calendar-timeline`), para o evento não
 * mudar de cor entre o painel e a tela de agenda. Classes inteiras, e
 * não montadas por string, porque o Tailwind só gera o que encontra
 * escrito.
 */
const COR_DA_CATEGORIA: Record<NonNullable<AgeEvent["color"]>, string> = {
  tomate: "bg-[rgb(var(--ev-tomate))]",
  flamingo: "bg-[rgb(var(--ev-flamingo))]",
  tangerina: "bg-[rgb(var(--ev-tangerina))]",
  banana: "bg-[rgb(var(--ev-banana))]",
  salvia: "bg-[rgb(var(--ev-salvia))]",
  manjericao: "bg-[rgb(var(--ev-manjericao))]",
  pavao: "bg-[rgb(var(--ev-pavao))]",
  mirtilo: "bg-[rgb(var(--ev-mirtilo))]",
  lavanda: "bg-[rgb(var(--ev-lavanda))]",
  uva: "bg-[rgb(var(--ev-uva))]",
  grafite: "bg-[rgb(var(--ev-grafite))]",
};

function corDoEvento(evento: AgeEvent): string {
  if (evento.color) return COR_DA_CATEGORIA[evento.color];
  if (evento.flexibility === "fixed") return "bg-fixed";
  if (evento.flexibility === "semi_flexible") return "bg-warn";
  return "bg-flexible";
}

/**
 * Eventos e propostas da janela, separados por dia e em ordem.
 *
 * As propostas entram porque é o que a pessoa mais quer ver ao lado da
 * conversa: "isso que o Age está sugerindo bate com o que eu já tenho?".
 * Um proposto não existe em `/calendar/events` (só os aplicados), então
 * ele vem dos itens pendentes dos planos, como a tela de agenda faz.
 */
export function agendaPorDia(
  eventos: AgeEvent[],
  planos: Plan[],
  timezone: string,
  agora: Date = new Date(),
  dias = 2,
): { dia: number; itens: ItemDaAgenda[] }[] {
  const itens: ItemDaAgenda[] = eventos.map((e) => ({
    id: e.id,
    titulo: e.title,
    start: e.start,
    end: e.end,
    diaInteiro: e.all_day,
    local: e.location,
    cor: corDoEvento(e),
    proposto: false,
  }));

  for (const plano of planos) {
    if (plano.status !== "proposed") continue;
    for (const item of plano.items) {
      if (item.status !== "pending" || item.target !== "event") continue;
      const depois = item.after as {
        start?: string;
        end?: string;
        title?: string;
      } | null;
      if (!depois?.start || !depois.end) continue;
      itens.push({
        id: `proposto:${item.id}`,
        titulo: depois.title ?? "(proposto)",
        start: depois.start,
        end: depois.end,
        diaInteiro: false,
        local: null,
        cor: "bg-proposed",
        proposto: true,
      });
    }
  }

  const hoje = new TZDate(agora, timezone);
  const porDia = new Map<number, ItemDaAgenda[]>();
  for (const item of itens) {
    const dia = differenceInCalendarDays(inZone(item.start, timezone), hoje);
    // Evento que começou ontem e termina hoje conta como de hoje: ele
    // ocupa a manhã, e escondê-lo faria a manhã parecer livre.
    const diaVisivel = dia < 0 && new Date(item.end) > startOfDay(hoje) ? 0 : dia;
    if (diaVisivel < 0 || diaVisivel >= dias) continue;
    porDia.set(diaVisivel, [...(porDia.get(diaVisivel) ?? []), item]);
  }

  return Array.from({ length: dias }, (_, dia) => ({
    dia,
    itens: (porDia.get(dia) ?? []).sort((a, b) => {
      // Dia inteiro primeiro, como em qualquer agenda.
      if (a.diaInteiro !== b.diaInteiro) return a.diaInteiro ? -1 : 1;
      return a.start.localeCompare(b.start);
    }),
  }));
}

/**
 * "3h20 livres no resto do dia · maior bloco 19:00–21:00".
 *
 * O maior bloco vai junto porque o total sozinho engana: 3h em doze
 * pedaços de 15 minutos não comportam uma sessão de estudo, e é isso
 * que a pessoa quer saber ao olhar.
 */
export function textoDoTempoLivre(livre: FreeTime, timezone: string): string {
  if (livre.free_minutes === 0 || livre.longest === null) {
    return "Nada livre no resto do dia, dentro do seu horário útil.";
  }
  const total = `${duration(livre.free_minutes)} ${
    livre.free_minutes === 60 ? "livre" : "livres"
  } no resto do dia`;
  if (livre.intervals.length === 1) {
    return `${total}, das ${timeOf(livre.longest.start, timezone)} às ${timeOf(
      livre.longest.end,
      timezone,
    )}.`;
  }
  return `${total} · maior bloco ${timeOf(livre.longest.start, timezone)}–${timeOf(
    livre.longest.end,
    timezone,
  )} (${duration(livre.longest.minutes)}).`;
}

/** O compromisso está acontecendo agora? */
export function acontecendoAgora(
  item: ItemDaAgenda,
  agora: Date = new Date(),
): boolean {
  return new Date(item.start) <= agora && agora < new Date(item.end);
}

// ---------------------------------------------------------------
// O painel
// ---------------------------------------------------------------

export function PainelLateral({
  aba,
  timezone,
  onAba,
  onFechar,
  onPedir,
  pedindo = false,
}: {
  aba: AbaDoPainel;
  timezone: string;
  onAba: (aba: AbaDoPainel) => void;
  onFechar: () => void;
  /**
   * Manda um pedido para a conversa, como se a pessoa tivesse escrito.
   *
   * É o que liga o painel ao chat: a tarefa sem horário ganha um botão
   * que pede ao Age para encaixá-la, e a proposta volta para aprovação
   * como qualquer outra (§5.8) — nada é marcado direto daqui.
   */
  onPedir?: (texto: string) => void;
  /** Um turno está em curso: pedir agora atropelaria a resposta. */
  pedindo?: boolean;
}) {
  // Esc fecha só no modo sobreposto, onde o painel cobre a conversa. Ao
  // lado, Esc é do composer e dos diálogos, e fechar o painel por
  // engano seria pior do que não ter o atalho.
  useEffect(() => {
    const ouvir = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (globalThis.matchMedia?.("(min-width: 1280px)").matches) return;
      if (document.querySelector("dialog[open], [role='dialog']")) return;
      onFechar();
    };
    globalThis.addEventListener("keydown", ouvir);
    return () => globalThis.removeEventListener("keydown", ouvir);
  }, [onFechar]);

  return (
    <>
      {/* Fundo do modo sobreposto: abaixo de 1280px não há largura para
          três colunas e a conversa, então o painel cobre o chat. */}
      <div
        aria-hidden
        onClick={onFechar}
        className="fixed inset-0 z-40 bg-canvas/60 backdrop-blur-[2px] xl:hidden"
      />
      <aside
        aria-label="Painel lateral"
        className={cx(
          "fixed inset-y-0 right-0 z-50 flex w-full flex-col sm:max-w-sm border-l border-line bg-surface shadow-2xl",
          "animate-fade-in xl:static xl:z-auto xl:w-80 xl:max-w-none xl:shrink-0 xl:shadow-none",
        )}
      >
        <div className="flex items-center gap-1 border-b border-line px-3 py-2.5">
          <div role="tablist" aria-label="O que mostrar" className="flex flex-1 gap-1">
            <Aba ativa={aba === "tarefas"} onClick={() => onAba("tarefas")}>
              <IconTasks className="h-[15px] w-[15px]" />
              Tarefas
            </Aba>
            <Aba ativa={aba === "agenda"} onClick={() => onAba("agenda")}>
              <IconCalendar className="h-[15px] w-[15px]" />
              Agenda
            </Aba>
          </div>
          <button
            onClick={onFechar}
            aria-label="Fechar painel"
            className="grid h-8 w-8 place-items-center rounded-lg text-muted transition-colors hover:bg-surface-2 hover:text-ink"
          >
            <IconX className="h-4 w-4" />
          </button>
        </div>

        <div role="tabpanel" className="min-h-0 flex-1 overflow-y-auto">
          {aba === "tarefas" ? (
            <AbaTarefas timezone={timezone} onPedir={onPedir} pedindo={pedindo} />
          ) : (
            <AbaAgenda timezone={timezone} />
          )}
        </div>
      </aside>
    </>
  );
}

function Aba({
  ativa,
  onClick,
  children,
}: {
  ativa: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      role="tab"
      aria-selected={ativa}
      onClick={onClick}
      className={cx(
        "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm transition-colors",
        /* Tinta da marca, e não `surface-2`: com o fundo cinza quase
           igual ao do painel, a aba ativa foi lida errado numa captura
           — parecia que a troca não tinha acontecido. */
        ativa
          ? "bg-brand/12 font-medium text-brand"
          : "text-muted hover:bg-surface-2/60 hover:text-ink",
      )}
    >
      {children}
    </button>
  );
}

function Rodape({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <div className="px-4 pb-5 pt-1">
      <Link
        href={href}
        className="text-xs font-medium text-brand transition-opacity hover:opacity-80"
      >
        {children} →
      </Link>
    </div>
  );
}

// ---------------------------------------------------------------
// Tarefas
// ---------------------------------------------------------------

function AbaTarefas({
  timezone,
  onPedir,
  pedindo,
}: {
  timezone: string;
  onPedir?: (texto: string) => void;
  pedindo: boolean;
}) {
  const queryClient = useQueryClient();

  // Mesma chave e mesma rota da tela de Tarefas com "mostrar
  // concluídas" desligado: o cache é um só.
  const tarefas = useQuery({
    queryKey: ["tasks", false],
    queryFn: () => api.get<Task[]>("/tasks", { status: "open" }),
  });
  const listas = useQuery({
    queryKey: ["task-lists"],
    queryFn: () => api.get<TaskListRef[]>("/tasks/lists"),
  });

  const [erro, setErro] = useState<string | null>(null);

  const alternar = useMutation({
    mutationFn: (t: Task) =>
      api.post(`/tasks/${t.id}/${t.status === "done" ? "reopen" : "complete"}`),
    onSuccess: () => {
      setErro(null);
      void queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
    onError: (err) =>
      setErro(
        err instanceof RequestFailed ? err.message : "Não consegui marcar essa tarefa.",
      ),
  });

  // Agrupar pelo prazo, a mesma regra da tela cheia: atrasadas no topo.
  const grupos = useMemo(
    () => groupTasks(tarefas.data ?? [], "due", listas.data ?? [], timezone),
    [tarefas.data, listas.data, timezone],
  );

  return (
    <div className="space-y-4 pt-3">
      <CriarRapido />

      {erro ? (
        <p
          role="alert"
          className="mx-3 flex items-start gap-2 rounded-lg bg-danger/10 px-3 py-2 text-xs text-danger ring-1 ring-inset ring-danger/25"
        >
          <IconAlert className="mt-px h-3.5 w-3.5 shrink-0" />
          {erro}
        </p>
      ) : null}

      {tarefas.isError ? (
        <div className="px-3">
          <LoadError
            message="Não consegui carregar suas tarefas."
            onRetry={() => void tarefas.refetch()}
          />
        </div>
      ) : tarefas.isLoading ? (
        <div className="px-3">
          <SkeletonList rows={4} />
        </div>
      ) : grupos.length === 0 ? (
        <p className="px-4 py-6 text-center text-sm leading-relaxed text-muted">
          Nada em aberto. Peça na conversa — &ldquo;preciso entregar o relatório
          sexta&rdquo; vira tarefa com prazo.
        </p>
      ) : (
        grupos.map((grupo) => (
          <section key={grupo.id}>
            <h3 className="flex items-center gap-2 px-4 pb-1">
              <span className="eyebrow">{grupo.label}</span>
              <span className="tnum ml-auto text-xs text-faint">
                {grupo.tasks.length}
              </span>
            </h3>
            <ul>
              {grupo.tasks.map((t) => (
                <LinhaDeTarefa
                  key={t.id}
                  tarefa={t}
                  timezone={timezone}
                  ocupado={alternar.isPending && alternar.variables?.id === t.id}
                  onAlternar={() => alternar.mutate(t)}
                  onPedir={onPedir}
                  pedindo={pedindo}
                />
              ))}
            </ul>
          </section>
        ))
      )}

      <Rodape href="/tasks">Abrir Tarefas</Rodape>
    </div>
  );
}

/**
 * Uma tarefa em uma ou duas linhas.
 *
 * Mostra o que a tela cheia mostra de mais importante (§8.1): o prazo
 * e se há tempo reservado. O aviso de "vence logo e não tem horário"
 * continua aqui, em âmbar — é o sinal que o produto existe para dar, e
 * ao lado da conversa é exatamente onde dá para resolver: basta pedir.
 */
function LinhaDeTarefa({
  tarefa,
  timezone,
  ocupado,
  onAlternar,
  onPedir,
  pedindo,
}: {
  tarefa: Task;
  timezone: string;
  ocupado: boolean;
  onAlternar: () => void;
  onPedir?: (texto: string) => void;
  pedindo: boolean;
}) {
  const feita = tarefa.status === "done";
  const prazo =
    tarefa.due_at ?? (tarefa.due_date ? `${tarefa.due_date}T23:59:59Z` : null);
  const agora = new Date();
  const situacao = situacaoDoHorario(tarefa, agora);
  // O próximo bloco que AINDA NÃO acabou. Antes era o mais cedo de
  // todos, e um bloco de um mês atrás aparecia em verde como se fosse
  // o horário da tarefa.
  const proximoBloco = tarefa.reserved
    .filter((b) => new Date(b.end) > agora)
    .sort((a, b) => a.start.localeCompare(b.start))[0];
  const horarioPassou = !feita && situacao === "passou";
  const precisaDeHorario =
    !feita &&
    situacao === "sem" &&
    prazo !== null &&
    (tarefa.is_overdue || daysUntil(prazo, timezone) <= 7);

  return (
    <li className="flex items-start gap-2.5 px-4 py-2 transition-colors hover:bg-surface-2/60">
      <label className="relative mt-0.5 flex shrink-0 cursor-pointer items-center">
        <input
          type="checkbox"
          checked={feita}
          disabled={ocupado}
          onChange={onAlternar}
          aria-label={feita ? `Reabrir ${tarefa.title}` : `Concluir ${tarefa.title}`}
          className="peer sr-only"
        />
        <span
          aria-hidden
          className={cx(
            "grid h-[18px] w-[18px] place-items-center rounded-md border-2 transition-all duration-150",
            "peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-brand",
            feita
              ? "border-ok bg-ok text-canvas"
              : "border-line-strong text-transparent hover:border-muted",
            ocupado && "opacity-50",
          )}
        >
          <IconCheck className="h-2.5 w-2.5" strokeWidth={3} />
        </span>
      </label>

      <div className="min-w-0 flex-1">
        <p
          className={cx(
            "text-sm leading-snug",
            feita ? "text-muted line-through" : "text-ink",
          )}
        >
          {tarefa.title}
        </p>
        <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs">
          {prazo ? (
            <span
              className={cx(
                "tnum",
                tarefa.is_overdue && !feita ? "font-medium text-danger" : "text-muted",
              )}
            >
              {dueLabel(prazo, timezone)}
            </span>
          ) : null}
          {proximoBloco ? (
            <span className="tnum flex items-center gap-1 text-ok">
              <IconClock className="h-3 w-3" />
              {quando(proximoBloco.start, timezone)}
            </span>
          ) : horarioPassou ? (
            <span className="flex items-center gap-1 font-medium text-warn">
              <IconAlert className="h-3 w-3" />o horário passou
            </span>
          ) : precisaDeHorario ? (
            <span className="flex items-center gap-1 font-medium text-warn">
              <IconAlert className="h-3 w-3" />
              sem horário
            </span>
          ) : null}
        </p>
        {onPedir && (horarioPassou || precisaDeHorario) ? (
          <button
            onClick={() => onPedir(pedidoDeHorario(tarefa, horarioPassou))}
            disabled={pedindo}
            title={pedindo ? "Espere o Age terminar de responder" : undefined}
            className="mt-1.5 flex items-center gap-1 rounded-md bg-brand/10 px-2 py-1 text-xs font-medium text-brand transition-colors hover:bg-brand/15 disabled:opacity-50"
          >
            <IconSparkle className="h-3 w-3" />
            pedir horário ao Age
          </button>
        ) : null}
      </div>
    </li>
  );
}

/**
 * O texto que vai para a conversa quando a pessoa pede horário.
 *
 * Escrito na primeira pessoa, como ela escreveria: a mensagem aparece
 * no histórico, e um comando com cara de sistema ali destoaria. Leva o
 * título entre aspas para o Age achar a tarefa certa entre as parecidas,
 * e a estimativa quando existe, para ele não chutar a duração.
 */
export function pedidoDeHorario(
  tarefa: Pick<Task, "title" | "estimated_min">,
  passou: boolean,
): string {
  const leva = tarefa.estimated_min
    ? ` Leva uns ${duration(tarefa.estimated_min)}.`
    : "";
  return passou
    ? `O horário que eu tinha reservado para "${tarefa.title}" passou e não terminei. Encontra um novo horário na agenda.${leva}`
    : `Reserva um horário na agenda para a tarefa "${tarefa.title}".${leva}`;
}

/** `hoje 14:00`, `amanhã 09:30` ou `qui 14:00`: curto, para caber. */
function quando(iso: string, timezone: string): string {
  const dias = daysUntil(iso, timezone);
  const hora = timeOf(iso, timezone);
  if (dias === 0) return `hoje ${hora}`;
  if (dias === 1) return `amanhã ${hora}`;
  return `${dayOf(iso, timezone)} ${hora}`;
}

/**
 * Criar com uma linha só: título e Enter.
 *
 * Prazo, prioridade e estimativa ficam para a tela cheia ou para a
 * conversa. Aqui o caso é "lembrei de uma coisa no meio do assunto", e
 * um formulário de quatro campos faria a pessoa deixar para depois.
 */
function CriarRapido() {
  const queryClient = useQueryClient();
  const [titulo, setTitulo] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const campo = useRef<HTMLInputElement>(null);

  const criar = useMutation({
    mutationFn: async (t: string) => {
      /*
       * Espera a lista padrão em vez de ler o que já chegou.
       *
       * O campo aparece antes de `/tasks/lists` responder, e quem abre o
       * painel e digita rápido mandava o POST sem `list_id`. O teste
       * pegou isso; na tela não daria erro, só uma tarefa na lista
       * errada. `ensureQueryData` usa o cache quando ele existe.
       */
      const listas = await queryClient.ensureQueryData({
        queryKey: ["task-lists"],
        queryFn: () => api.get<TaskListRef[]>("/tasks/lists"),
      });
      return api.post<Task>("/tasks", {
        title: t,
        list_id: listas.find((l) => l.is_default)?.id,
      });
    },
    onSuccess: () => {
      setTitulo("");
      setErro(null);
      void queryClient.invalidateQueries({ queryKey: ["tasks"] });
      campo.current?.focus();
    },
    onError: (err) =>
      setErro(err instanceof RequestFailed ? err.message : "Não consegui criar."),
  });

  function enviar(e: FormEvent) {
    e.preventDefault();
    const t = titulo.trim();
    if (t && !criar.isPending) criar.mutate(t);
  }

  return (
    <form onSubmit={enviar} className="px-3">
      <div className="flex items-center gap-2 rounded-lg border border-line bg-surface-2/50 px-2.5 transition-colors focus-within:border-brand/60 focus-within:bg-surface">
        <IconPlus className="h-3.5 w-3.5 text-muted" />
        <label htmlFor="painel-nova-tarefa" className="sr-only">
          Nova tarefa
        </label>
        <input
          ref={campo}
          id="painel-nova-tarefa"
          value={titulo}
          onChange={(e) => setTitulo(e.target.value)}
          maxLength={500}
          placeholder="Nova tarefa — Enter para criar"
          disabled={criar.isPending}
          className="min-w-0 flex-1 bg-transparent py-2 text-sm text-ink placeholder:text-faint focus-visible:outline-none"
        />
      </div>
      {erro ? (
        <p role="alert" className="mt-1.5 px-1 text-xs text-danger">
          {erro}
        </p>
      ) : null}
    </form>
  );
}

// ---------------------------------------------------------------
// Agenda
// ---------------------------------------------------------------

function AbaAgenda({ timezone }: { timezone: string }) {
  /*
   * O "agora" anda sozinho, de minuto em minuto.
   *
   * O painel fica aberto ao lado da conversa por horas: sem isto, o
   * destaque de "agora" ficaria preso no compromisso de quando a tela
   * abriu, e passada a meia-noite "hoje" seria ontem.
   */
  const [agora, setAgora] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setAgora(new Date()), 60_000);
    return () => clearInterval(t);
  }, []);

  // A janela muda só quando o DIA muda, e não a cada minuto: com o
  // minuto na chave, cada tique viraria uma consulta nova.
  const diaDeHoje = startOfDay(new TZDate(agora, timezone)).toISOString();
  const janela = useMemo(
    () => janelaDoPainel(timezone, new Date(diaDeHoje)),
    [timezone, diaDeHoje],
  );

  const eventos = useQuery({
    queryKey: ["events", janela.start, janela.end],
    queryFn: () =>
      api.get<{ events: AgeEvent[] }>("/calendar/events", {
        start: janela.start,
        end: janela.end,
      }),
  });

  // Mesma chave da consulta do chat: a proposta que aparece no cartão
  // de aprovação aparece aqui no mesmo instante, tracejada.
  const propostas = useQuery({
    queryKey: ["plan", "propostos"],
    queryFn: () => api.get<Plan[]>("/plans?status=proposed"),
    staleTime: 10_000,
  });

  /*
   * Quanto sobra livre de agora até o fim de hoje.
   *
   * O começo é arredondado em 15 minutos: com o minuto exato na chave,
   * o relógio de minuto em minuto viraria uma consulta nova a cada
   * tique. Para CIMA, e não para baixo: para baixo, o painel contaria
   * como livres minutos que já passaram.
   *
   * A chave começa com `"events"` de propósito: aprovar um plano, mover
   * ou apagar um compromisso já invalidam `["events"]`, e o número
   * acompanha sem código novo.
   */
  const desde = new Date(Math.ceil(agora.getTime() / 900_000) * 900_000).toISOString();
  const fimDeHoje = janelaDoPainel(timezone, new Date(diaDeHoje), 1).end;
  const livre = useQuery({
    queryKey: ["events", "livre", desde, fimDeHoje],
    queryFn: () =>
      api.get<FreeTime>("/calendar/free-time", { start: desde, end: fimDeHoje }),
    // Nos últimos minutos do dia o arredondamento passa da meia-noite, e
    // uma janela invertida é 400 no servidor.
    enabled: desde < fimDeHoje,
  });

  const dias = useMemo(
    () =>
      agendaPorDia(eventos.data?.events ?? [], propostas.data ?? [], timezone, agora),
    [eventos.data, propostas.data, timezone, agora],
  );

  if (eventos.isError) {
    return (
      <div className="p-3">
        <LoadError
          message="Não consegui carregar a agenda."
          onRetry={() => void eventos.refetch()}
        />
      </div>
    );
  }
  if (eventos.isLoading) {
    return (
      <div className="p-3">
        <SkeletonList rows={4} />
      </div>
    );
  }

  return (
    <div className="space-y-4 pt-3">
      {dias.map(({ dia, itens }) => (
        <section key={dia}>
          <h3 className="flex items-baseline gap-2 px-4 pb-1">
            <span className="eyebrow">{dia === 0 ? "Hoje" : "Amanhã"}</span>
            <span className="text-xs text-faint">
              {format(
                addDays(inZone(agora.toISOString(), timezone), dia),
                "EEEE, d 'de' MMMM",
                { locale: ptBR },
              )}
            </span>
          </h3>
          {dia === 0 && livre.data ? (
            <p className="mx-4 mb-2 mt-1 rounded-lg bg-surface-2 px-3 py-2 text-xs leading-relaxed text-ink-2">
              {textoDoTempoLivre(livre.data, timezone)}
            </p>
          ) : null}
          {itens.length === 0 ? (
            <p className="px-4 py-2 text-sm text-muted">Nada marcado.</p>
          ) : (
            <ul>
              {itens.map((item) => (
                <LinhaDaAgenda
                  key={item.id}
                  item={item}
                  timezone={timezone}
                  agora={agora}
                />
              ))}
            </ul>
          )}
        </section>
      ))}

      <Rodape href="/calendar">Abrir Agenda</Rodape>
    </div>
  );
}

function LinhaDaAgenda({
  item,
  timezone,
  agora,
}: {
  item: ItemDaAgenda;
  timezone: string;
  agora: Date;
}) {
  const passou = new Date(item.end) <= agora;
  const agoraMesmo = acontecendoAgora(item, agora);

  return (
    <li
      className={cx(
        "mx-2 flex items-stretch gap-3 rounded-lg px-2 py-2",
        agoraMesmo && "bg-brand/8 ring-1 ring-inset ring-brand/20",
        passou && "opacity-55",
      )}
    >
      <span
        aria-hidden
        className={cx(
          "w-[3px] shrink-0 rounded-full",
          item.cor,
          item.proposto && "age-hatch opacity-80",
        )}
      />
      <span className="tnum w-11 shrink-0 pt-px text-xs text-muted">
        {item.diaInteiro ? "dia todo" : timeOf(item.start, timezone)}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm leading-snug text-ink">
          {item.titulo}
          {item.proposto ? (
            <span className="ml-1.5 rounded-md border border-dashed border-proposed/60 px-1.5 py-px align-middle text-2xs font-medium text-proposed">
              proposta
            </span>
          ) : null}
        </p>
        <p className="mt-0.5 text-xs text-muted">
          {/* Só o fim: o começo já está na coluna da esquerda, e
              repeti-lo foi a primeira coisa que a captura mostrou. */}
          {item.diaInteiro ? null : `até ${timeOf(item.end, timezone)}`}
          {agoraMesmo ? (
            <span className="ml-1.5 font-medium text-brand">agora</span>
          ) : null}
          {item.local ? <span className="block truncate">{item.local}</span> : null}
        </p>
      </div>
    </li>
  );
}
