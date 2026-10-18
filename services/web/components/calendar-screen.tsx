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
  type Proposal,
  type TimelineView,
} from "@/components/calendar-timeline/calendar-timeline";
import {
  Badge,
  Button,
  Dialog,
  IconChevronLeft,
  IconChevronRight,
  IconLock,
  IconSparkle,
  LoadError,
  Select,
  cx,
} from "@/components/ui";
import { PlanReview } from "@/components/plan-review/plan-review";
import { RequestFailed, api } from "@/lib/api-client";
import { mensagemDeFalha } from "@/lib/resultado-do-apply";
import { range } from "@/lib/format";
import { guardarUltimaVista, lerUltimaVista } from "@/lib/ultimo-lugar";
import type { AgeEvent, ApplyResult, Flexibility, Plan } from "@/lib/types";

const VIEWS = [
  { value: "day" as const, label: "dia" },
  { value: "week" as const, label: "semana" },
  { value: "month" as const, label: "mês" },
];

const FLEX_LABEL: Record<Flexibility, string> = {
  fixed: "Fixo: não movo",
  semi_flexible: "Semi-flexível: movo avisando",
  flexible: "Flexível: movo quando precisar",
};

export function CalendarScreen({ timezone }: { timezone: string }) {
  const queryClient = useQueryClient();
  const [view, setViewState] = useState<TimelineView>("week");
  const [cursor, setCursor] = useState(() => new Date());
  const [selected, setSelected] = useState<AgeEvent | null>(null);

  /*
   * A visualização volta como a pessoa deixou (§8.1).
   *
   * Lido num efeito, não no `useState`: o servidor pré-renderiza esta
   * tela e não tem `localStorage`, então ler durante a renderização
   * daria divergência de hidratação. É o mesmo cuidado do tema.
   *
   * A DATA não é restaurada de propósito — sempre abre em hoje.
   */
  useEffect(() => {
    const salva = lerUltimaVista();
    if (salva !== null) setViewState(salva);
  }, []);

  // Troca de visualização passa por aqui para ser lembrada. Um
  // `setViewState` solto em qualquer um dos dois pontos de troca
  // voltaria a esquecer, e em silêncio.
  const setView = useCallback((proxima: TimelineView) => {
    setViewState(proxima);
    guardarUltimaVista(proxima);
  }, []);

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

  /**
   * Os planos que ainda tem item pendente: os OBJETOS, nao os itens.
   *
   * `proposals` acima achata tudo em itens para a timeline desenhar os
   * blocos hachurados, e joga o plano fora. O painel herdou isso, e por
   * isso so sabia descartar e remarcar: sem o plano nao ha id para
   * `POST /plans/{id}/apply`, e o botao de aprovar simplesmente nao
   * existia nesta tela.
   *
   * O relato era esse beco: o cartao do chat morria com a conversa, e a
   * agenda, que MOSTRA as propostas, nao oferecia como aceita-las.
   */
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

  // Painel de gerência das propostas, aberto pelo selo do cabeçalho.
  const [gerenciando, setGerenciando] = useState(false);
  const [remarcando, setRemarcando] = useState<Proposal | null>(null);
  const [novoInicio, setNovoInicio] = useState("");
  const [ocupado, setOcupado] = useState(false);
  // O que falhou ao descartar. Sem isto, falha de rede virava "pronto".
  const [erroDescarte, setErroDescarte] = useState<string | null>(null);

  async function recarregarPropostas() {
    await queryClient.invalidateQueries({ queryKey: ["plans", "proposed"] });
  }

  async function descartar(itemId: string) {
    setOcupado(true);
    setErroDescarte(null);
    try {
      await api.del(`/plans/items/${itemId}`);
      await recarregarPropostas();
    } catch (err) {
      // Sem este `catch` a exceção subia e a pessoa via o painel
      // intacto, sem nenhuma indicação de que o descarte não foi.
      setErroDescarte(
        err instanceof RequestFailed
          ? err.message
          : "Não consegui descartar essa proposta. Tente de novo.",
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
      setErroDescarte(
        err instanceof RequestFailed
          ? err.message
          : "Nao consegui descartar esta proposta.",
      );
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
      const aviso = mensagemDeFalha(result);
      if (aviso !== null) {
        // O painel FICA aberto quando algo nao entrou: fecha-lo
        // esconderia o motivo, que e o defeito que estamos consertando.
        setErroDescarte(aviso);
      } else if (planosPendentes.length <= 1) {
        setGerenciando(false);
      }
    } catch (err) {
      setErroDescarte(
        err instanceof RequestFailed ? err.message : "Nao consegui aplicar.",
      );
    } finally {
      setOcupado(false);
    }
  }

  async function descartarTudo() {
    setOcupado(true);
    try {
      // Em série: o rate limit do core-api responde 429 ao excedente, e
      // um lote paralelo deixaria parte das propostas na tela depois do
      // "descartei tudo".
      //
      // O erro de UMA proposta não interrompe as outras (a pessoa pediu
      // para descartar todas), mas é contado: antes o `catch` engolia
      // tudo e a tela fechava dizendo que descartou mesmo que as 8
      // chamadas tivessem falhado.
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
        // O painel fica aberto: fechar esconderia o que não saiu.
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
    // Preserva a duração original: a pessoa escolhe quando começa, e
    // mexer no tamanho do bloco sem ela pedir seria outra decisão.
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

  /**
   * O título do período, que a barra da lib dava e agora é nosso.
   *
   * Na semana, o intervalo pode atravessar dois meses ("29 set – 5 out")
   * e é isso que a pessoa precisa ler; repetir o mês nas duas pontas
   * quando é o mesmo seria ruído.
   */
  const periodLabel = useMemo(() => {
    if (view === "month") return format(cursor, "MMMM yyyy", { locale: ptBR });
    if (view === "day") return format(cursor, "EEEE, d 'de' MMMM", { locale: ptBR });
    const from = startOfWeek(cursor, { locale: ptBR });
    const to = addDays(from, 6);
    return from.getMonth() === to.getMonth()
      ? `${format(from, "d")} – ${format(to, "d 'de' MMMM", { locale: ptBR })}`
      : `${format(from, "d 'de' MMM", { locale: ptBR })} – ${format(to, "d 'de' MMM", {
          locale: ptBR,
        })}`;
  }, [cursor, view]);

  function shift(direction: -1 | 1) {
    setCursor((current) =>
      view === "month"
        ? addMonths(current, direction)
        : addWeeks(current, view === "day" ? direction / 7 : direction),
    );
  }

  return (
    <div className="flex h-dvh flex-col pb-16 md:pb-0">
      {/*
       * A barra da agenda é a nossa, e a da lib foi desligada
       * (`toolbar: () => null`). Antes as duas apareciam: dois botões
       * de "hoje", duas formas de trocar a visão, e só uma delas com o
       * estilo do produto.
       */}
      <header className="glass sticky top-0 z-20 border-b border-line">
        <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3.5 md:px-6">
          <div className="min-w-0">
            <h1 className="flex items-center gap-2 text-xl font-semibold">
              {/* `first-letter:uppercase`, não `capitalize`: este último
                  põe maiúscula em cada palavra e produz "4 – 10 De
                  Outubro". Só a primeira letra é que deve subir. */}
              <span className="inline-block truncate first-letter:uppercase">
                {periodLabel}
              </span>
              {proposals.length > 0 ? (
                /*
                 * O selo era só informativo, e o relato foi "está lá
                 * como proposta, não posso mais alterar, não posso mais
                 * editar". A agenda desenhava os blocos hachurados e
                 * não oferecia uma única ação sobre eles: só dava para
                 * aprovar ou rejeitar o plano INTEIRO, e apenas pelo
                 * cartão do turno que os criou — que some assim que a
                 * conversa avança.
                 *
                 * Agora o selo é a porta: abre a lista com horário, e
                 * cada item pode ser remarcado ou descartado sozinho.
                 */
                <button
                  onClick={() => setGerenciando(true)}
                  className="rounded-full transition-opacity hover:opacity-80"
                  aria-label={`Gerenciar ${proposals.length} propostas`}
                >
                  <Badge tone="proposed">
                    {proposals.length} proposto
                    {proposals.length === 1 ? "" : "s"}
                  </Badge>
                </button>
              ) : null}
            </h1>
          </div>

          <div className="ml-auto flex items-center gap-2">
            {/* Grupo segmentado para a visão: três opções que se
                excluem, e o estado atual visível sem abrir nada. */}
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

            <div className="flex items-center gap-0.5">
              <Button
                size="icon"
                variant="ghost"
                onClick={() => shift(-1)}
                aria-label="Período anterior"
              >
                <IconChevronLeft />
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setCursor(new Date())}>
                hoje
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
          </div>
        </div>
      </header>

      <div className="mx-auto min-h-0 w-full max-w-6xl flex-1 px-3 py-3 md:px-6 md:py-4">
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

        {/*
         * O cartao de aprovacao, aqui dentro.
         *
         * Este painel se chamava "Propostas esperando aprovacao" e oferecia
         * Remarcar, Descartar e Descartar todas. Aprovar, nao: um grep por
         * `apply` neste arquivo devolvia zero. Era a segunda metade do
         * relato, depois do cartao do chat que sumia.
         *
         * Reusa o `PlanReview` do chat em vez de um botao "aprovar tudo"
         * proprio: aprovacao granular e primeira-classe (§5.8, §8.1), e uma
         * segunda superficie com semantica mais fraca divergiria da primeira.
         *
         * Um cartao por plano, empilhados. `confirm_blocking`, `item_ids` e
         * a janela de undo sao POR PLANO: juntar itens de planos diferentes
         * num cartao so mentiria sobre atomicidade.
         */}
        {planosPendentes.length > 0 ? (
          <div className="mb-4 space-y-3">
            {planosPendentes.length > 1 ? (
              <p className="text-xs text-muted">
                {planosPendentes.length} propostas separadas. Cada uma e aprovada por
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

        {/*
         * A lista abaixo existe so para REMARCAR, que o cartao nao faz.
         *
         * Ao ver a tela pronta, as mesmas propostas apareciam duas vezes:
         * uma no cartao, com caixa de selecao, e outra aqui com Remarcar e
         * Descartar. Aprovar e descartar agora moram no cartao, entao aqui
         * fica so o que falta, sem repetir a decisao.
         */}
        {proposals.length === 0 ? (
          <p className="text-sm text-muted">Nenhuma proposta pendente.</p>
        ) : (
          <>
            <p className="mb-2 text-xs text-muted">
              Precisa remarcar ou tirar alguma antes de aprovar?
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
                      // `datetime-local` não aceita offset: o valor tem de
                      // ser a hora local já formatada, sem o fuso.
                      setNovoInicio(proposta.start.slice(0, 16));
                    }}
                  >
                    Remarcar
                  </Button>
                  {/* Descartar UM item continua util aqui: no cartao, tirar
                    um item e desmarcar a caixa, que o deixa pendente em
                    vez de remove-lo. */}
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
        open={selected !== null}
        onClose={() => setSelected(null)}
        title={selected?.title ?? ""}
        description={
          selected ? range(selected.start, selected.end, timezone) : undefined
        }
        footer={
          selected ? (
            <>
              <Button variant="ghost" onClick={() => setSelected(null)}>
                Fechar
              </Button>
              <Button variant="danger" onClick={() => void remove(selected.id)}>
                Apagar
              </Button>
            </>
          ) : null
        }
      >
        {selected ? (
          <div className="space-y-4">
            {selected.location || selected.created_by_age ? (
              <div className="flex flex-wrap items-center gap-2">
                {selected.location ? (
                  <span className="text-xs text-muted">{selected.location}</span>
                ) : null}
                {selected.created_by_age ? (
                  <Badge tone="brand">
                    <IconSparkle className="h-2.5 w-2.5" />
                    criado pelo Age
                  </Badge>
                ) : null}
              </div>
            ) : null}

            {/* O cadeado de §8.1. Fica num `<Select>` em vez de num
                ícone de duas posições porque são TRÊS estados (§5.6), e
                um cadeado aberto/fechado não representa o meio. */}
            <div className="rounded-xl bg-surface-2 p-4">
              <label
                htmlFor="flex"
                className="flex items-center gap-2 text-sm font-medium"
              >
                <IconLock className="h-4 w-4 text-muted" />O Age pode mover este evento?
              </label>
              <Select
                id="flex"
                value={selected.flexibility}
                onChange={(e) =>
                  void setFlexibility(selected.id, e.target.value as Flexibility)
                }
                className="mt-2.5"
              >
                {(Object.keys(FLEX_LABEL) as Flexibility[]).map((value) => (
                  <option key={value} value={value}>
                    {FLEX_LABEL[value]}
                  </option>
                ))}
              </Select>
              <p className="mt-2.5 text-xs leading-relaxed text-muted">
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
    { className: "age-ev age-ev-flexible", label: "flexível", lock: false },
    { className: "age-ev age-ev-semi", label: "semi-flexível", lock: false },
    { className: "age-ev age-ev-fixed", label: "fixo", lock: true },
    { className: "age-ev age-ev-proposed age-hatch", label: "proposto", lock: false },
  ];
  return (
    <div className="age-timeline flex flex-wrap items-center justify-center gap-x-4 gap-y-2 border-t border-line bg-surface px-4 py-2.5 text-xs text-muted md:px-6">
      {items.map((item) => (
        <span key={item.label} className="flex items-center gap-1.5">
          <span aria-hidden className={`${item.className} h-3 w-6 rounded`} />
          {item.label}
          {item.lock ? <IconLock className="h-3 w-3" /> : null}
        </span>
      ))}
    </div>
  );
}
