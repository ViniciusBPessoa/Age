/**
 * A agenda (§8.1), repaginada para marcar e organizar sem sair dela.
 *
 * O que a pessoa faz aqui, e de onde veio cada padrão:
 *
 * - **Clicar ou arrastar num horário vazio** abre a criação rápida ao
 *   lado, como no Google Agenda: título, cor, Enter.
 * - **Arrastar um bloco** muda o horário; **puxar a borda** muda a
 *   duração. Grava na hora (§8.0) e oferece "Desfazer" no rodapé, como
 *   o Gmail.
 * - **Arrastar uma tarefa** da barra lateral para a grade vira proposta
 *   de reserva no lugar onde caiu, e abre a revisão para aprovar.
 * - **Cor por categoria** e **cadeado de flexibilidade** num toque, no
 *   balão do evento.
 * - **Atalhos de teclado** (N, T, D/S/M/L, setas), como no Google Agenda
 *   e no Notion Calendar. `?` mostra a lista.
 * - No celular: abre no dia, o botão de criar flutua ao alcance do
 *   polegar (Samsung One UI), e os balões viram folha de baixo.
 *
 * O que já existia e continua: propostas hachuradas na grade, painel
 * para aprovar, remarcar e descartar, e o cadeado de três estados (§5.6).
 */

"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addDays,
  addMonths,
  addWeeks,
  endOfMonth,
  endOfWeek,
  format,
  startOfMonth,
  startOfWeek,
} from "date-fns";
import { ptBR } from "date-fns/locale";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  CalendarTimeline,
  type Ancora,
  type Proposal,
  type TimelineView,
} from "@/components/calendar-timeline/calendar-timeline";
import { EventoDialog } from "@/components/calendar-timeline/evento-dialog";
import { Aviso, type AvisoAgenda } from "@/components/calendar/aviso";
import { Balao } from "@/components/calendar/balao";
import {
  BarraLateral,
  type TarefaArrastada,
} from "@/components/calendar/barra-lateral";
import { CriacaoRapida } from "@/components/calendar/criacao-rapida";
import { DetalhesDoEvento } from "@/components/calendar/detalhes-do-evento";
import { PlanReview } from "@/components/plan-review/plan-review";
import {
  Badge,
  Button,
  Dialog,
  IconChevronLeft,
  IconChevronRight,
  IconPlus,
  LoadError,
  cx,
} from "@/components/ui";
import { RequestFailed, api } from "@/lib/api-client";
import { ATALHOS_DA_AGENDA, useAtalhos } from "@/lib/atalhos";
import { range } from "@/lib/format";
import { mensagemDeFalha } from "@/lib/resultado-do-apply";
import { guardarUltimaVista, lerUltimaVista } from "@/lib/ultimo-lugar";
import type { AgeEvent, ApplyResult, EventColor, Flexibility, Plan } from "@/lib/types";

const VIEWS: { value: TimelineView; label: string; tecla: string }[] = [
  { value: "day", label: "dia", tecla: "D" },
  { value: "week", label: "semana", tecla: "S" },
  { value: "month", label: "mês", tecla: "M" },
  { value: "agenda", label: "lista", tecla: "L" },
];

/** Dias que a visão de lista mostra a partir do dia escolhido. */
const DIAS_DA_LISTA = 30;

type Criando = { inicio: Date; fim: Date; ancora: Ancora | null };
type CriandoCompleto = {
  inicio?: Date;
  fim?: Date;
  titulo?: string;
  cor?: EventColor | null;
};

function mensagem(err: unknown, padrao: string): string {
  return err instanceof RequestFailed ? err.message : padrao;
}

export function CalendarScreen({ timezone }: { timezone: string }) {
  const queryClient = useQueryClient();
  const [view, setViewState] = useState<TimelineView>("week");
  /*
   * A data de hoje so e decidida no CLIENTE: `new Date()` no render roda
   * no servidor e no navegador com relogios diferentes, e o React
   * acusava divergencia de hidratacao em `date={Date}`.
   */
  const [cursor, setCursor] = useState<Date | null>(null);

  useEffect(() => {
    const salva = lerUltimaVista();
    if (salva !== null) {
      setViewState(salva);
    } else if (globalThis.matchMedia?.("(max-width: 640px)").matches) {
      // No celular, sete colunas em 390px nao cabem: o padrao e o dia.
      setViewState("day");
    }
    setCursor(new Date());
  }, []);

  const setView = useCallback((proxima: TimelineView) => {
    setViewState(proxima);
    guardarUltimaVista(proxima);
  }, []);

  const dia = cursor ?? new Date(0);

  // A janela buscada é maior que a visível nas bordas do mês.
  const janela = useMemo(() => {
    if (view === "agenda") {
      return {
        start: dia.toISOString(),
        end: addDays(dia, DIAS_DA_LISTA).toISOString(),
      };
    }
    const base =
      view === "month"
        ? { from: startOfMonth(dia), to: endOfMonth(dia) }
        : { from: dia, to: dia };
    return {
      start: startOfWeek(base.from, { locale: ptBR }).toISOString(),
      end: endOfWeek(base.to, { locale: ptBR }).toISOString(),
    };
  }, [dia, view]);

  const events = useQuery({
    queryKey: ["events", janela.start, janela.end],
    queryFn: () =>
      api.get<{ events: AgeEvent[] }>("/calendar/events", {
        start: janela.start,
        end: janela.end,
      }),
  });

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

  const planosPendentes = useMemo(
    () =>
      (plans.data ?? []).filter(
        (p) => p.status === "proposed" && p.items.some((i) => i.status === "pending"),
      ),
    [plans.data],
  );

  const byId = useMemo(() => {
    const map = new Map<string, AgeEvent>();
    for (const event of events.data?.events ?? []) map.set(event.id, event);
    return map;
  }, [events.data]);

  // ---------------------------------------------------------------
  // Estado da tela
  // ---------------------------------------------------------------
  const [selecionado, setSelecionado] = useState<{ id: string; ancora: Ancora } | null>(
    null,
  );
  const evento = selecionado ? (byId.get(selecionado.id) ?? null) : null;
  const [editando, setEditando] = useState<AgeEvent | null>(null);
  const [criandoRapido, setCriandoRapido] = useState<Criando | null>(null);
  const [criandoCompleto, setCriandoCompleto] = useState<CriandoCompleto | null>(null);
  const [arrastando, setArrastando] = useState<TarefaArrastada | null>(null);
  const [aviso, setAviso] = useState<AvisoAgenda | null>(null);
  const [ajuda, setAjuda] = useState(false);

  const [gerenciando, setGerenciando] = useState(false);
  const [remarcando, setRemarcando] = useState<Proposal | null>(null);
  const [novoInicio, setNovoInicio] = useState("");
  const [ocupado, setOcupado] = useState(false);
  const [erroDescarte, setErroDescarte] = useState<string | null>(null);

  const avisar = useCallback(
    (texto: string, extra?: Omit<AvisoAgenda, "id" | "texto">) =>
      setAviso({ id: Date.now(), texto, ...extra }),
    [],
  );
  const sumirAviso = useCallback(() => setAviso(null), []);

  const recarregarEventos = useCallback(
    () => queryClient.invalidateQueries({ queryKey: ["events"] }),
    [queryClient],
  );
  async function recarregarPropostas() {
    await queryClient.invalidateQueries({ queryKey: ["plans", "proposed"] });
  }

  // ---------------------------------------------------------------
  // Edição direta (§8.0), sempre com volta
  // ---------------------------------------------------------------
  async function patchEvento(id: string, corpo: Record<string, unknown>) {
    await api.patch<AgeEvent>(`/calendar/events/${id}`, corpo);
    await recarregarEventos();
  }

  async function aoSoltar(
    id: string,
    kind: "event" | "proposal",
    inicio: Date,
    fim: Date,
    acao: "mover" | "duracao",
  ) {
    if (kind === "proposal") {
      // Continua proposta: só muda o horário sugerido, como o "Remarcar".
      try {
        await api.patch(`/plans/items/${id}`, {
          start: inicio.toISOString(),
          end: fim.toISOString(),
        });
        await recarregarPropostas();
        avisar("Proposta remarcada. Ela ainda espera sua aprovação.");
      } catch (err) {
        avisar(mensagem(err, "Não consegui remarcar a proposta."), { tom: "erro" });
      }
      return;
    }
    const antes = byId.get(id);
    if (!antes) return;
    // Otimista: a grade já mostra onde o bloco caiu, sem esperar a rede.
    queryClient.setQueryData<{ events: AgeEvent[] }>(
      ["events", janela.start, janela.end],
      (velho) =>
        velho
          ? {
              ...velho,
              events: velho.events.map((e) =>
                e.id === id
                  ? { ...e, start: inicio.toISOString(), end: fim.toISOString() }
                  : e,
              ),
            }
          : velho,
    );
    try {
      await patchEvento(id, { start: inicio.toISOString(), end: fim.toISOString() });
      avisar(
        acao === "mover"
          ? `"${antes.title}" foi para ${range(inicio.toISOString(), fim.toISOString(), timezone)}.`
          : `"${antes.title}" agora vai até ${format(fim, "HH:mm")}.`,
        {
          desfazer: async () => {
            await patchEvento(id, { start: antes.start, end: antes.end });
          },
        },
      );
    } catch (err) {
      await recarregarEventos();
      avisar(mensagem(err, "Não consegui mudar o horário."), { tom: "erro" });
    }
  }

  async function mudarCor(ev: AgeEvent, cor: EventColor | null) {
    try {
      await patchEvento(ev.id, { color: cor });
    } catch (err) {
      avisar(mensagem(err, "Não consegui trocar a cor."), { tom: "erro" });
    }
  }

  async function mudarFlex(ev: AgeEvent, flexibility: Flexibility) {
    try {
      await api.patch<AgeEvent>(`/calendar/events/${ev.id}/flexibility`, {
        flexibility,
      });
      await recarregarEventos();
    } catch (err) {
      avisar(mensagem(err, "Não consegui mudar a flexibilidade."), { tom: "erro" });
    }
  }

  function corpoDe(ev: AgeEvent) {
    return {
      title: ev.title,
      start: ev.start,
      end: ev.end,
      description: ev.description,
      location: ev.location,
      all_day: ev.all_day,
      flexibility: ev.flexibility,
      color: ev.color,
    };
  }

  async function apagar(ev: AgeEvent) {
    setOcupado(true);
    try {
      await api.del(`/calendar/events/${ev.id}`);
      setSelecionado(null);
      await recarregarEventos();
      avisar(`"${ev.title}" apagado.`, {
        desfazer: async () => {
          await api.post<AgeEvent>("/calendar/events", corpoDe(ev));
          await recarregarEventos();
        },
      });
    } catch (err) {
      avisar(mensagem(err, "Não consegui apagar."), { tom: "erro" });
    } finally {
      setOcupado(false);
    }
  }

  async function duplicar(ev: AgeEvent) {
    // Mesmo horário na semana seguinte: é o "repetir isto" mais comum.
    const semana = 7 * 86_400_000;
    setOcupado(true);
    try {
      const copia = await api.post<AgeEvent>("/calendar/events", {
        ...corpoDe(ev),
        start: new Date(new Date(ev.start).getTime() + semana).toISOString(),
        end: new Date(new Date(ev.end).getTime() + semana).toISOString(),
      });
      setSelecionado(null);
      await recarregarEventos();
      avisar(`Cópia criada na semana seguinte.`, {
        desfazer: async () => {
          await api.del(`/calendar/events/${copia.id}`);
          await recarregarEventos();
        },
      });
    } catch (err) {
      avisar(mensagem(err, "Não consegui duplicar."), { tom: "erro" });
    } finally {
      setOcupado(false);
    }
  }

  // Tarefa solta na grade: proposta de reserva + revisão aberta.
  async function soltarTarefa(inicio: Date, fim: Date) {
    const tarefa = arrastando;
    setArrastando(null);
    if (!tarefa) return;
    try {
      await api.post<{ plan_id: string }>(`/tasks/${tarefa.id}/schedule`, {
        start: inicio.toISOString(),
        end: fim.toISOString(),
        title: tarefa.titulo,
      });
      await Promise.all([
        recarregarPropostas(),
        queryClient.invalidateQueries({ queryKey: ["tasks"] }),
      ]);
      setGerenciando(true);
    } catch (err) {
      avisar(mensagem(err, "Não consegui reservar esse horário."), { tom: "erro" });
    }
  }

  // ---------------------------------------------------------------
  // Propostas (o painel que já existia)
  // ---------------------------------------------------------------
  async function descartar(itemId: string) {
    setOcupado(true);
    setErroDescarte(null);
    try {
      await api.del(`/plans/items/${itemId}`);
      await recarregarPropostas();
    } catch (err) {
      setErroDescarte(
        mensagem(err, "Não consegui descartar essa proposta. Tente de novo."),
      );
    } finally {
      setOcupado(false);
    }
  }

  async function descartarPlano(plano: Plan) {
    setOcupado(true);
    setErroDescarte(null);
    try {
      await api.post(`/plans/${plano.id}/reject`);
      await queryClient.invalidateQueries({ queryKey: ["plans"] });
    } catch (err) {
      setErroDescarte(mensagem(err, "Não consegui descartar esta proposta."));
    } finally {
      setOcupado(false);
    }
  }

  async function aplicar(plano: Plan, itemIds: string[], confirmBlocking: boolean) {
    setOcupado(true);
    setErroDescarte(null);
    try {
      const result = await api.post<ApplyResult>(`/plans/${plano.id}/apply`, {
        item_ids: itemIds,
        confirm_blocking: confirmBlocking,
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["plans"] }),
        queryClient.invalidateQueries({ queryKey: ["events"] }),
        queryClient.invalidateQueries({ queryKey: ["tasks"] }),
      ]);
      const falha = mensagemDeFalha(result);
      if (falha !== null) {
        setErroDescarte(falha);
      } else if (planosPendentes.length <= 1) {
        setGerenciando(false);
        avisar("Pronto, está na sua agenda.");
      }
    } catch (err) {
      setErroDescarte(mensagem(err, "Não consegui aplicar."));
    } finally {
      setOcupado(false);
    }
  }

  async function descartarTudo() {
    setOcupado(true);
    try {
      // Em série: o rate limit do core-api responde 429 ao excedente.
      let falhas = 0;
      for (const proposta of proposals) {
        try {
          await api.del(`/plans/items/${proposta.id}`);
        } catch {
          falhas += 1;
        }
      }
      await recarregarPropostas();
      if (falhas > 0) {
        setErroDescarte(
          falhas === proposals.length
            ? "Não consegui descartar as propostas. Tente de novo."
            : `Descartei parte delas: ${falhas} não saíram.`,
        );
        return;
      }
      setErroDescarte(null);
      setGerenciando(false);
    } finally {
      setOcupado(false);
    }
  }

  async function salvarRemarcacao() {
    if (!remarcando || !novoInicio) return;
    const duracao =
      new Date(remarcando.end).getTime() - new Date(remarcando.start).getTime();
    const inicio = new Date(novoInicio);
    setOcupado(true);
    try {
      await api.patch(`/plans/items/${remarcando.id}`, {
        start: inicio.toISOString(),
        end: new Date(inicio.getTime() + duracao).toISOString(),
      });
      await recarregarPropostas();
      setRemarcando(null);
    } finally {
      setOcupado(false);
    }
  }

  // ---------------------------------------------------------------
  // Navegação e atalhos
  // ---------------------------------------------------------------
  const periodLabel = useMemo(() => {
    if (view === "month") return format(dia, "MMMM yyyy", { locale: ptBR });
    if (view === "day") {
      // No celular o nome inteiro do dia não cabe ao lado dos botões.
      const curto = globalThis.matchMedia?.("(max-width: 640px)").matches;
      return curto
        ? `${["dom", "seg", "ter", "qua", "qui", "sex", "sáb"][dia.getDay()]}, ${format(dia, "d MMM", { locale: ptBR })}`
        : format(dia, "EEEE, d 'de' MMMM", { locale: ptBR });
    }
    if (view === "agenda")
      return `${format(dia, "d 'de' MMM", { locale: ptBR })} – ${format(
        addDays(dia, DIAS_DA_LISTA),
        "d 'de' MMM",
        { locale: ptBR },
      )}`;
    const from = startOfWeek(dia, { locale: ptBR });
    const to = addDays(from, 6);
    return from.getMonth() === to.getMonth()
      ? `${format(from, "d")} – ${format(to, "d 'de' MMMM", { locale: ptBR })}`
      : `${format(from, "d 'de' MMM", { locale: ptBR })} – ${format(to, "d 'de' MMM", {
          locale: ptBR,
        })}`;
  }, [dia, view]);

  const shift = useCallback(
    (direction: -1 | 1) => {
      setCursor((current) => {
        const base = current ?? new Date();
        if (view === "month") return addMonths(base, direction);
        if (view === "day") return addDays(base, direction);
        if (view === "agenda") return addDays(base, direction * 7);
        return addWeeks(base, direction);
      });
    },
    [view],
  );

  const novo = useCallback(() => {
    const agora = new Date();
    const inicio = new Date(cursor ?? agora);
    inicio.setHours(agora.getHours() + 1, 0, 0, 0);
    setCriandoCompleto({ inicio, fim: new Date(inicio.getTime() + 3600_000) });
  }, [cursor]);

  const atalhos = useMemo(
    () => ({
      n: novo,
      t: () => setCursor(new Date()),
      d: () => setView("day"),
      s: () => setView("week"),
      w: () => setView("week"),
      m: () => setView("month"),
      l: () => setView("agenda"),
      a: () => setView("agenda"),
      j: () => shift(1),
      k: () => shift(-1),
      ArrowRight: () => shift(1),
      ArrowLeft: () => shift(-1),
      "?": () => setAjuda(true),
    }),
    [novo, setView, shift],
  );
  const algoAberto =
    selecionado !== null ||
    criandoRapido !== null ||
    criandoCompleto !== null ||
    editando !== null ||
    gerenciando ||
    ajuda;
  useAtalhos(atalhos, cursor !== null && !algoAberto);

  if (cursor === null) {
    return <div className="flex h-dvh flex-col pb-16 md:pb-0" />;
  }

  return (
    <div className="flex h-dvh flex-col pb-16 md:pb-0">
      <header className="glass sticky top-0 z-20 border-b border-line">
        <div className="flex w-full flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3 md:px-6">
          <div className="flex min-w-0 items-center gap-2">
            <Button size="sm" variant="secondary" onClick={() => setCursor(new Date())}>
              Hoje
            </Button>
            <div className="flex items-center">
              <Button
                size="icon"
                variant="ghost"
                onClick={() => shift(-1)}
                aria-label="Período anterior"
              >
                <IconChevronLeft />
              </Button>
              <Button
                size="icon"
                variant="ghost"
                onClick={() => shift(1)}
                aria-label="Próximo período"
              >
                <IconChevronRight />
              </Button>
            </div>
            <h1 className="flex min-w-0 items-center gap-2 text-lg font-semibold sm:text-xl">
              <span className="inline-block truncate first-letter:uppercase">
                {periodLabel}
              </span>
              {proposals.length > 0 ? (
                <button
                  onClick={() => setGerenciando(true)}
                  className="rounded-full transition-opacity hover:opacity-80"
                  aria-label={`Gerenciar ${proposals.length} propostas`}
                >
                  <Badge tone="proposed">
                    <span className="whitespace-nowrap">
                      {proposals.length} proposto
                      {proposals.length === 1 ? "" : "s"}
                    </span>
                  </Badge>
                </button>
              ) : null}
            </h1>
          </div>

          <div className="ml-auto flex items-center gap-2">
            <div
              role="group"
              aria-label="Visão"
              className="flex rounded-lg bg-surface-2 p-0.5 ring-1 ring-inset ring-line"
            >
              {VIEWS.map((option) => (
                <button
                  key={option.value}
                  onClick={() => setView(option.value)}
                  aria-pressed={view === option.value}
                  aria-keyshortcuts={option.tecla}
                  title={`${option.label} (${option.tecla})`}
                  className={cx(
                    "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                    view === option.value
                      ? "bg-surface text-ink shadow-ring"
                      : "text-muted hover:text-ink",
                  )}
                >
                  {option.label}
                </button>
              ))}
            </div>
            <Button
              size="sm"
              variant="primary"
              className="hidden sm:inline-flex lg:hidden"
              onClick={novo}
              aria-keyshortcuts="N"
            >
              <IconPlus className="h-3.5 w-3.5" />
              novo
            </Button>
          </div>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <BarraLateral
          cursor={cursor}
          timezone={timezone}
          onEscolherDia={(d) => {
            setCursor(d);
            if (view === "month") setView("day");
          }}
          onNovo={novo}
          onArrastarTarefa={setArrastando}
          onAjuda={() => setAjuda(true)}
        />

        <div className="min-h-0 min-w-0 flex-1 px-2 py-2 md:px-4 md:py-3">
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
              onSelectEvent={(id, kind, ancora) => {
                if (kind === "proposal") {
                  setGerenciando(true);
                  return;
                }
                setCriandoRapido(null);
                setSelecionado({ id, ancora });
              }}
              onCriarNoGrid={(inicio, fim, ancora) => {
                setSelecionado(null);
                setCriandoRapido({ inicio, fim, ancora });
              }}
              onEventDrop={(id, kind, inicio, fim, acao) =>
                void aoSoltar(id, kind, inicio, fim, acao)
              }
              onSoltarDeFora={(inicio, fim) => void soltarTarefa(inicio, fim)}
              arrastandoDeFora={
                arrastando
                  ? { titulo: arrastando.titulo, minutos: arrastando.minutos }
                  : null
              }
            />
          )}
        </div>
      </div>

      {/* Botão flutuante no celular, ao alcance do polegar (One UI). */}
      <button
        type="button"
        onClick={novo}
        aria-label="Novo compromisso"
        className="fixed bottom-[calc(4.75rem+env(safe-area-inset-bottom))] right-[max(1rem,env(safe-area-inset-right))] z-30 grid h-14 w-14 place-items-center rounded-2xl bg-brand text-brand-ink shadow-float transition-transform active:scale-95 sm:hidden"
      >
        <IconPlus className="h-6 w-6" />
      </button>

      <Balao
        aberto={criandoRapido !== null}
        ancora={criandoRapido?.ancora ?? null}
        onFechar={() => setCriandoRapido(null)}
        rotulo="Novo compromisso"
      >
        {criandoRapido ? (
          <CriacaoRapida
            inicio={criandoRapido.inicio}
            fim={criandoRapido.fim}
            timezone={timezone}
            onCriado={(ev) => {
              setCriandoRapido(null);
              avisar(`"${ev.title}" marcado.`, {
                desfazer: async () => {
                  await api.del(`/calendar/events/${ev.id}`);
                  await recarregarEventos();
                },
              });
            }}
            onMaisOpcoes={(titulo, cor) => {
              setCriandoCompleto({
                inicio: criandoRapido.inicio,
                fim: criandoRapido.fim,
                titulo,
                cor,
              });
              setCriandoRapido(null);
            }}
          />
        ) : null}
      </Balao>

      <Balao
        aberto={evento !== null}
        ancora={selecionado?.ancora ?? null}
        onFechar={() => setSelecionado(null)}
        rotulo={evento?.title ?? "Compromisso"}
      >
        {evento ? (
          <DetalhesDoEvento
            evento={evento}
            timezone={timezone}
            ocupado={ocupado}
            onEditar={() => {
              setEditando(evento);
              setSelecionado(null);
            }}
            onDuplicar={() => void duplicar(evento)}
            onApagar={() => void apagar(evento)}
            onCor={(cor) => void mudarCor(evento, cor)}
            onFlex={(f) => void mudarFlex(evento, f)}
          />
        ) : null}
      </Balao>

      {editando ? (
        <EventoDialog
          evento={editando}
          timezone={timezone}
          onClose={() => setEditando(null)}
        />
      ) : null}

      {criandoCompleto ? (
        <EventoDialog
          timezone={timezone}
          inicioSugerido={criandoCompleto.inicio}
          fimSugerido={criandoCompleto.fim}
          tituloSugerido={criandoCompleto.titulo}
          corSugerida={criandoCompleto.cor}
          onClose={() => setCriandoCompleto(null)}
        />
      ) : null}

      <Dialog
        open={gerenciando}
        onClose={() => (ocupado ? undefined : setGerenciando(false))}
        size="lg"
        title="Propostas esperando aprovação"
        description="Nada aqui está na sua agenda ainda."
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => setGerenciando(false)}
              disabled={ocupado}
            >
              Fechar
            </Button>
            <Button
              variant="danger"
              onClick={() => void descartarTudo()}
              disabled={ocupado || proposals.length === 0}
            >
              {ocupado ? "Descartando…" : "Descartar todas"}
            </Button>
          </>
        }
      >
        {erroDescarte ? (
          <p
            role="alert"
            className="mb-3 rounded-xl bg-danger/10 px-3.5 py-2.5 text-xs leading-relaxed text-danger ring-1 ring-inset ring-danger/25"
          >
            {erroDescarte}
          </p>
        ) : null}

        {/* Um cartão por plano: `item_ids`, `confirm_blocking` e o undo são
            POR PLANO, e juntar itens de planos diferentes mentiria sobre
            atomicidade (§5.8). */}
        {planosPendentes.length > 0 ? (
          <div className="mb-4 space-y-3">
            {planosPendentes.length > 1 ? (
              <p className="text-xs text-muted">
                {planosPendentes.length} propostas separadas. Cada uma é aprovada por
                conta.
              </p>
            ) : null}
            {planosPendentes.map((plano) => (
              <PlanReview
                key={plano.id}
                plan={plano}
                timezone={timezone}
                applying={ocupado}
                onApply={(itemIds, confirmBlocking) =>
                  void aplicar(plano, itemIds, confirmBlocking)
                }
                onReject={() => void descartarPlano(plano)}
              />
            ))}
          </div>
        ) : null}

        {proposals.length === 0 ? (
          <p className="text-sm text-muted">Nenhuma proposta pendente.</p>
        ) : (
          <>
            <p className="mb-2 text-xs text-muted">
              Precisa remarcar ou tirar alguma antes de aprovar? Também dá para arrastar
              o bloco hachurado na agenda.
            </p>
            <ul className="max-h-60 space-y-1.5 overflow-y-auto">
              {proposals.map((proposta) => (
                <li
                  key={proposta.id}
                  className="flex items-center gap-2 rounded-lg border border-line px-2.5 py-2"
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm text-ink">{proposta.title}</p>
                    <p className="text-2xs text-muted">
                      {range(proposta.start, proposta.end, timezone)}
                    </p>
                  </div>
                  <Button
                    variant="quiet"
                    size="sm"
                    disabled={ocupado}
                    onClick={() => {
                      setRemarcando(proposta);
                      setNovoInicio(proposta.start.slice(0, 16));
                    }}
                  >
                    Remarcar
                  </Button>
                  <Button
                    variant="quiet"
                    size="sm"
                    disabled={ocupado}
                    onClick={() => void descartar(proposta.id)}
                    aria-label={`Descartar ${proposta.title}`}
                  >
                    Descartar
                  </Button>
                </li>
              ))}
            </ul>
          </>
        )}
      </Dialog>

      <Dialog
        open={remarcando !== null}
        onClose={() => (ocupado ? undefined : setRemarcando(null))}
        title="Remarcar proposta"
        description={remarcando?.title}
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => setRemarcando(null)}
              disabled={ocupado}
            >
              Cancelar
            </Button>
            <Button
              variant="primary"
              onClick={() => void salvarRemarcacao()}
              disabled={ocupado || !novoInicio}
            >
              Salvar
            </Button>
          </>
        }
      >
        <label htmlFor="novo-inicio" className="mb-1.5 block text-xs text-muted">
          Novo início (a duração não muda)
        </label>
        <input
          id="novo-inicio"
          type="datetime-local"
          value={novoInicio}
          onChange={(e) => setNovoInicio(e.target.value)}
          className="w-full rounded-lg border border-line-strong bg-surface-2 px-3 py-2 text-sm text-ink"
        />
      </Dialog>

      <Dialog
        open={ajuda}
        onClose={() => setAjuda(false)}
        title="Atalhos da agenda"
        description="Funcionam quando nenhum campo de texto está em foco."
      >
        <dl className="grid grid-cols-[auto_1fr] items-center gap-x-4 gap-y-2.5 text-sm">
          {ATALHOS_DA_AGENDA.map((a) => (
            <div key={a.teclas} className="contents">
              <dt>
                <kbd className="rounded-md border border-line-strong bg-surface-2 px-2 py-0.5 font-mono text-xs">
                  {a.teclas}
                </kbd>
              </dt>
              <dd className="text-ink-2">{a.o_que}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-4 text-xs leading-relaxed text-muted">
          Com o mouse: clique num horário vazio para marcar, arraste um bloco para mudar
          o horário, puxe a borda de baixo para mudar a duração, e arraste uma tarefa da
          barra lateral para reservar tempo para ela.
        </p>
      </Dialog>

      <Aviso aviso={aviso} onSumir={sumirAviso} />
    </div>
  );
}
