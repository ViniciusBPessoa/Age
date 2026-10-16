/**
 * Revisão de plano — a aprovação granular de §8.1 e §5.8.
 *
 * "Aprovação é **granular**: aceitar 4 de 6 itens é válido. Os não
 * aceitos podem alimentar um recálculo."
 *
 * Por isso o botão principal diz "Aplicar N selecionados", e não
 * "Aplicar". A diferença não é cosmética: um botão que diz "aplicar"
 * sobre uma lista parcialmente marcada é ambíguo justamente no momento
 * em que a pessoa está decidindo o que entra na agenda dela.
 *
 * O item com conflito bloqueante exige confirmação separada (§5.6). O
 * backend recusa sem `confirm_blocking`, e a tela precisa dizer o que
 * está em jogo antes de oferecer a caixa — "confirmar" sem saber o que
 * é bloqueado é consentimento vazio.
 *
 * Sobre a aparência, que é o que mudou aqui: esta é a tela onde se
 * autoriza escrita na agenda, e a versão anterior a desenhava como uma
 * lista de caixas de 13px com texto cinza. Agora o item selecionado
 * fica visivelmente selecionado (fundo tingido e anel), a caixa de
 * marcação tem tamanho de alvo de toque, e o cartão inteiro é
 * clicável — porque a decisão item por item é a ação central do
 * produto, não um detalhe de formulário.
 */

"use client";

import { useMemo, useState } from "react";

import {
  Badge,
  Button,
  IconAlert,
  IconCalendar,
  IconCheck,
  IconClock,
  IconLink,
  IconSparkle,
  IconTasks,
  cx,
} from "@/components/ui";
import { duration, range } from "@/lib/format";
import type { Plan, PlanItem, PlanOp } from "@/lib/types";

/**
 * O rótulo de cada operação, em português.
 *
 * As chaves são MAIÚSCULAS porque é assim que a API manda. A versão
 * anterior usava minúsculas e o mapa nunca acertava — a tela mostrava
 * `CREATE` e `MOVE` crus, e pior, as comparações de `op` espalhadas
 * por este arquivo também falhavam.
 */
const OP_LABEL: Record<PlanOp, string> = {
  CREATE: "Criar",
  MOVE: "Mover",
  RESIZE: "Ajustar",
  DELETE: "Remover",
  COMPLETE: "Concluir",
  REORDER: "Reordenar",
  UPDATE: "Atualizar",
};

interface Payload {
  _kind?: string;
  title?: string;
  start?: string;
  end?: string;
  due_at?: string | null;
  due_date?: string | null;
  estimated_min?: number | null;
  flexibility?: string;
}

function payload(value: Record<string, unknown> | null): Payload | null {
  return (value as Payload | null) ?? null;
}

/**
 * O rótulo do botão de aplicar.
 *
 * §8.1 escreve "Aplicar 5 selecionados", e a palavra "selecionados"
 * carrega sentido: ela diz que o que vai entrar é o que está marcado, e
 * não o plano inteiro. Quando está tudo marcado a palavra vira ruído —
 * não há seleção a destacar — então o texto muda para falar do plano.
 */
function applyLabel(count: number, total: number): string {
  if (count === total) {
    return total === 1 ? "Aplicar o item" : `Aplicar os ${total} itens`;
  }
  return count === 1 ? "Aplicar 1 selecionado" : `Aplicar ${count} selecionados`;
}

export function PlanReview({
  plan,
  timezone,
  applying,
  onApply,
  onReject,
  onReschedule,
  onAskChange,
}: {
  plan: Plan;
  timezone: string;
  applying?: boolean;
  onApply: (itemIds: string[], confirmBlocking: boolean) => void;
  onReject: () => void;
  /** Remarca um item antes de aprovar. Ausente = o ajuste some. */
  onReschedule?: (itemId: string, start: string, end: string) => Promise<void>;
  /** Manda o pedido de mudança de volta ao Age, em palavras. */
  onAskChange?: (texto: string) => void;
}) {
  // Tudo marcado por padrão: o plano é uma proposta coerente, e obrigar
  // a marcar seis caixas para aceitar o que foi pedido é atrito contra
  // o caso comum. Desmarcar é a exceção, e exceção é que deve custar o
  // clique.
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(plan.items.filter((i) => i.status === "pending").map((i) => i.id)),
  );
  const [confirmBlocking, setConfirmBlocking] = useState(false);
  /*
   * O campo de "pede do seu jeito".
   *
   * Ajustar horário resolve o que é mecânico; isto resolve o resto:
   * "tira os de sábado", "prefiro de manhã", "corta pela metade". Sem
   * ele, a única saída para um pedido assim é rejeitar tudo e
   * redigitar a conversa inteira desde o começo.
   */
  const [pedindoAjuste, setPedindoAjuste] = useState(false);
  const [textoAjuste, setTextoAjuste] = useState("");

  const pending = plan.items.filter((item) => item.status === "pending");

  /**
   * O par ligado se aprova junto (§5.7.1).
   *
   * "Reservar tempo para uma tarefa cria DOIS itens ligados; aprovado,
   * aplicado e revertido como unidade." Deixar marcar só o evento
   * produziria um bloco de tempo sem tarefa nenhuma — o oposto do que a
   * pessoa pediu.
   */
  function toggle(item: PlanItem) {
    setSelected((previous) => {
      const next = new Set(previous);
      const group = [item.id, ...item.links];
      const turningOn = !next.has(item.id);
      for (const id of group) {
        if (turningOn) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  }

  const blockingSelected = useMemo(
    () => pending.filter((item) => item.is_blocking && selected.has(item.id)),
    [pending, selected],
  );

  const needsConfirm = blockingSelected.length > 0;
  /*
   * `!applying` faz parte da condição, e faltava.
   *
   * O botão de aprovar continuava clicável durante a aplicação, então
   * dois cliques mandavam dois `apply` do mesmo plano e podiam criar o
   * evento duas vezes na agenda. O de rejeitar, ao lado, já conferia
   * `applying` — a assimetria passou despercebida porque a tela
   * "parece" ocupada.
   *
   * Um clique a mais aqui escreve no calendário da pessoa, que é o que
   * o produto promete não fazer sem aprovação (§5.2).
   */
  const canApply =
    selected.size > 0 &&
    (!needsConfirm || confirmBlocking) &&
    !plan.is_expired &&
    !applying;
  const allSelected = selected.size === pending.length;

  if (plan.is_expired) {
    return (
      <div className="card flex items-start gap-3 p-4">
        <IconClock className="mt-0.5 h-4 w-4 shrink-0 text-muted" />
        <p className="text-sm leading-relaxed text-muted">
          Esta proposta expirou. A agenda pode ter mudado desde que ela foi feita, então
          aplicá-la agora poderia criar conflito — peça de novo e eu recalculo.
        </p>
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-2xl border border-proposed/30 bg-surface shadow-raised">
      {/* O cabeçalho usa a cor de "proposto" (§5.6): esta é a faixa que
          diz, antes de ler qualquer item, que nada disso está na agenda
          ainda. */}
      <div className="border-b border-line bg-proposed/8 px-4 py-3.5 md:px-5">
        <div className="flex items-start gap-3">
          <span
            aria-hidden
            className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-proposed/15 text-proposed"
          >
            <IconSparkle className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-base font-semibold">{plan.summary}</h3>
              <Badge tone="proposed">proposta</Badge>
            </div>
            {plan.origin_note ? (
              <p className="mt-1 text-xs leading-relaxed text-muted">
                {plan.origin_note}
              </p>
            ) : null}
          </div>
        </div>

        <div className="mt-3 flex items-center justify-between gap-3 text-xs">
          <span className="text-muted">
            {pending.length === 1 ? "1 item" : `${pending.length} itens`} ·{" "}
            <span className="font-medium text-ink-2">
              {selected.size} {selected.size === 1 ? "marcado" : "marcados"}
            </span>
          </span>
          <button
            onClick={() =>
              setSelected(
                allSelected ? new Set() : new Set(pending.map((item) => item.id)),
              )
            }
            className="font-medium text-brand transition-opacity hover:opacity-80"
          >
            {allSelected ? "Desmarcar tudo" : "Marcar tudo"}
          </button>
        </div>
      </div>

      <ul className="divide-y divide-line">
        {pending.map((item) => (
          <ItemCard
            key={item.id}
            item={item}
            timezone={timezone}
            checked={selected.has(item.id)}
            linked={item.links.length > 0}
            onToggle={() => toggle(item)}
            onReschedule={onReschedule}
          />
        ))}
      </ul>

      {needsConfirm ? (
        <div className="border-t border-warn/25 bg-warn/8 px-4 py-3.5 md:px-5">
          <label className="flex cursor-pointer items-start gap-3 text-xs leading-relaxed">
            <input
              type="checkbox"
              checked={confirmBlocking}
              onChange={(e) => setConfirmBlocking(e.target.checked)}
              className="mt-0.5 h-4 w-4 shrink-0 cursor-pointer accent-warn"
            />
            <span className="text-ink-2">
              <strong className="font-semibold text-warn">
                {blockingSelected.length === 1
                  ? "Um item escolhido tem conflito sério."
                  : `${blockingSelected.length} itens escolhidos têm conflito sério.`}
              </strong>{" "}
              {blockingSelected
                .map((item) => item.conflicts[0]?.message)
                .filter(Boolean)
                .join(" ")}{" "}
              Entendi e quero aplicar assim mesmo.
            </span>
          </label>
        </div>
      ) : null}

      {pedindoAjuste && onAskChange ? (
        <div className="border-t border-line bg-surface-2/60 px-4 py-3.5 md:px-5">
          <label
            htmlFor={`ajuste-${plan.id}`}
            className="mb-1.5 block text-xs text-muted"
          >
            O que você quer diferente? Eu refaço a proposta.
          </label>
          <textarea
            id={`ajuste-${plan.id}`}
            value={textoAjuste}
            onChange={(e) => setTextoAjuste(e.target.value)}
            onKeyDown={(e) => {
              // Enter envia, Shift+Enter quebra linha: é a convenção do
              // formato, e quem escreve uma frase curta não quer
              // procurar o botão.
              if (e.key === "Enter" && !e.shiftKey && textoAjuste.trim()) {
                e.preventDefault();
                onAskChange(textoAjuste.trim());
                setTextoAjuste("");
                setPedindoAjuste(false);
              }
            }}
            rows={2}
            autoFocus
            placeholder="ex.: tira os de sábado e deixa 1h em vez de 2h"
            className="w-full resize-none rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm text-ink placeholder:text-faint"
          />
          <div className="mt-2 flex gap-2">
            <Button
              size="sm"
              variant="primary"
              disabled={!textoAjuste.trim()}
              onClick={() => {
                onAskChange(textoAjuste.trim());
                setTextoAjuste("");
                setPedindoAjuste(false);
              }}
            >
              Pedir
            </Button>
            <Button size="sm" variant="quiet" onClick={() => setPedindoAjuste(false)}>
              Cancelar
            </Button>
          </div>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-2 border-t border-line bg-surface-2/40 px-4 py-3.5 md:px-5">
        <Button
          variant="primary"
          loading={applying}
          disabled={!canApply}
          onClick={() => onApply([...selected], confirmBlocking)}
        >
          {applying ? null : <IconCheck />}
          {applyLabel(selected.size, pending.length)}
        </Button>
        <Button variant="ghost" onClick={onReject} disabled={applying}>
          Rejeitar tudo
        </Button>
        {onAskChange ? (
          <Button
            variant="quiet"
            onClick={() => setPedindoAjuste((v) => !v)}
            disabled={applying}
          >
            Pedir mudança
          </Button>
        ) : null}
        {selected.size < pending.length && selected.size > 0 ? (
          <span className="text-xs text-muted">
            {pending.length - selected.size} fora, dá para pedir um recálculo depois.
          </span>
        ) : null}
      </div>
    </div>
  );
}

function ItemCard({
  item,
  timezone,
  checked,
  linked,
  onToggle,
  onReschedule,
}: {
  item: PlanItem;
  timezone: string;
  checked: boolean;
  linked: boolean;
  onToggle: () => void;
  onReschedule?: (itemId: string, start: string, end: string) => Promise<void>;
}) {
  const before = payload(item.before);
  const after = payload(item.after);
  const isTask = item.target === "task";
  const subject = after ?? before;
  const blocking = item.conflicts.some((c) => c.severity === "blocking");
  // Normaliza uma vez. Comparar `item.op` direto em sete lugares foi
  // como a divergência de caixa passou despercebida por tanto tempo.
  const op = item.op.toUpperCase() as PlanOp;

  return (
    <li
      className={cx(
        "relative transition-colors duration-150",
        checked ? "bg-brand/[0.055]" : "opacity-65 hover:opacity-100",
      )}
    >
      {/* A barra lateral no item marcado: dá para varrer a lista e ver
          o que entra sem reler cada caixa de marcação. */}
      {checked ? (
        <span aria-hidden className="absolute left-0 top-0 h-full w-[3px] bg-brand" />
      ) : null}

      <label className="flex cursor-pointer items-start gap-3 px-4 py-3.5 md:px-5">
        <input
          type="checkbox"
          checked={checked}
          onChange={onToggle}
          className="mt-0.5 h-[18px] w-[18px] shrink-0 cursor-pointer rounded accent-brand"
          aria-describedby={`reason-${item.id}`}
        />

        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span
              className={cx(
                "rounded-md px-1.5 py-0.5 text-2xs font-semibold uppercase tracking-[0.04em]",
                op === "DELETE"
                  ? "bg-danger/12 text-danger"
                  : op === "MOVE" || op === "RESIZE"
                    ? "bg-warn/12 text-warn"
                    : "bg-brand/12 text-brand",
              )}
            >
              {OP_LABEL[op] ?? item.op}
            </span>
            <span className="text-sm font-semibold text-ink">
              {subject?.title ?? "(sem título)"}
            </span>
            {/* Agenda ou lista: §5.7 insiste que são coisas diferentes,
                e o ícone é a pista mais rápida de qual é qual. */}
            <span className="inline-flex items-center gap-1 text-2xs text-muted">
              {isTask ? (
                <>
                  <IconTasks className="h-3 w-3" />
                  lista
                </>
              ) : (
                <>
                  <IconCalendar className="h-3 w-3" />
                  agenda
                </>
              )}
            </span>
            {linked ? (
              // §5.7.1: o par se aprova junto, e dizer isso evita a
              // surpresa de marcar um e ver o outro mudar.
              <Badge tone="brand">
                <IconLink className="h-2.5 w-2.5" />
                par ligado
              </Badge>
            ) : null}
          </span>

          {/* O quando. Tarefa mostra prazo; evento mostra o intervalo —
              são coisas diferentes, e §5.7 insiste nisso: prazo não
              ocupa a agenda. */}
          <span className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-2">
            <IconClock className="h-3 w-3 shrink-0 text-muted" />
            <span className="tnum">
              {op === "MOVE" && before?.start && after?.start ? (
                <>
                  <span className="text-muted line-through">
                    {range(before.start, before.end ?? before.start, timezone)}
                  </span>
                  {" → "}
                  {range(after.start, after.end ?? after.start, timezone)}
                </>
              ) : subject?.start && subject.end ? (
                range(subject.start, subject.end, timezone)
              ) : subject?.due_at ? (
                <>prazo {range(subject.due_at, subject.due_at, timezone)}</>
              ) : subject?.due_date ? (
                <>prazo {subject.due_date.split("-").reverse().join("/")}</>
              ) : isTask ? (
                "sem prazo"
              ) : null}
            </span>
            {subject?.estimated_min ? (
              <span className="text-muted">· {duration(subject.estimated_min)}</span>
            ) : null}
          </span>

          {/* O motivo. §5.7 exige que todo item carregue um — "porque o
              assistente disse" não é justificativa. */}
          <span
            id={`reason-${item.id}`}
            className="mt-1.5 block text-xs leading-relaxed text-muted"
          >
            {item.reason}
          </span>

          {item.conflicts.length > 0 ? (
            <span className="mt-2 block space-y-1">
              {item.conflicts.map((conflict, index) => (
                <span
                  key={index}
                  className={cx(
                    "flex items-start gap-1.5 rounded-lg px-2 py-1.5 text-xs leading-relaxed",
                    conflict.severity === "blocking"
                      ? "bg-danger/10 text-danger"
                      : "bg-warn/10 text-warn",
                  )}
                >
                  <IconAlert className="mt-px h-3 w-3 shrink-0" />
                  <span>{conflict.message}</span>
                </span>
              ))}
            </span>
          ) : null}

          {/* Flexibilidade semi do que está sendo movido: §8.1 mostra o
              aviso porque mover algo que a pessoa marcou como
              quase-fixo merece ser dito. */}
          {op === "MOVE" && before?.flexibility === "semi_flexible" && !blocking ? (
            <span className="mt-1.5 flex items-center gap-1.5 text-xs text-warn">
              <IconAlert className="h-3 w-3 shrink-0" />
              evento semi-flexível
            </span>
          ) : null}
        </span>
      </label>

      {/*
       * Ajustar sem sair da tela.
       *
       * Antes só havia marcar e desmarcar: "esse bloco serve, mas não
       * às 8h15" obrigava a rejeitar tudo e redigitar o pedido, o que
       * gasta um turno do modelo para mover meia hora.
       *
       * Fora do `<label>` de propósito: dentro dele, qualquer clique
       * alterna a caixa de marcação, e abrir o ajuste desmarcaria o
       * item que a pessoa está querendo manter.
       */}
      {onReschedule && !isTask && subject?.start && subject.end ? (
        <AjusteDeHorario
          item={item}
          inicio={subject.start}
          fim={subject.end}
          onSalvar={onReschedule}
        />
      ) : null}
    </li>
  );
}

function AjusteDeHorario({
  item,
  inicio,
  fim,
  onSalvar,
}: {
  item: PlanItem;
  inicio: string;
  fim: string;
  onSalvar: (itemId: string, start: string, end: string) => Promise<void>;
}) {
  const [aberto, setAberto] = useState(false);
  const [salvando, setSalvando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  // `datetime-local` não aceita offset de fuso, então o valor é a hora
  // local já formatada, sem o `-03:00` do fim da string.
  const [quando, setQuando] = useState(() => inicio.slice(0, 16));
  const [minutos, setMinutos] = useState(() =>
    Math.max(
      5,
      Math.round((new Date(fim).getTime() - new Date(inicio).getTime()) / 60000),
    ),
  );

  async function salvar() {
    setSalvando(true);
    setErro(null);
    try {
      const comeco = new Date(quando);
      if (Number.isNaN(comeco.getTime())) {
        setErro("Data inválida.");
        return;
      }
      await onSalvar(
        item.id,
        comeco.toISOString(),
        new Date(comeco.getTime() + minutos * 60000).toISOString(),
      );
      setAberto(false);
    } catch {
      setErro("Não consegui salvar. Tente de novo.");
    } finally {
      setSalvando(false);
    }
  }

  if (!aberto) {
    return (
      <div className="px-4 pb-3 pl-[46px] md:px-5 md:pl-[50px]">
        <button
          onClick={() => setAberto(true)}
          className="text-xs font-medium text-brand transition-opacity hover:opacity-80"
        >
          Ajustar horário
        </button>
      </div>
    );
  }

  return (
    <div className="border-t border-line bg-surface-2/60 px-4 py-3 pl-[46px] md:px-5 md:pl-[50px]">
      <div className="flex flex-wrap items-end gap-2">
        <span className="flex flex-col gap-1">
          <label htmlFor={`quando-${item.id}`} className="text-2xs text-muted">
            Começa
          </label>
          <input
            id={`quando-${item.id}`}
            type="datetime-local"
            value={quando}
            onChange={(e) => setQuando(e.target.value)}
            className="h-8 rounded-lg border border-line-strong bg-surface px-2 text-xs text-ink"
          />
        </span>
        <span className="flex flex-col gap-1">
          <label htmlFor={`dura-${item.id}`} className="text-2xs text-muted">
            Minutos
          </label>
          <input
            id={`dura-${item.id}`}
            type="number"
            min={5}
            step={5}
            value={minutos}
            onChange={(e) => setMinutos(Number(e.target.value))}
            className="h-8 w-20 rounded-lg border border-line-strong bg-surface px-2 text-xs text-ink"
          />
        </span>
        <Button
          size="sm"
          variant="secondary"
          onClick={() => void salvar()}
          disabled={salvando}
        >
          {salvando ? "Salvando…" : "Salvar"}
        </Button>
        <Button
          size="sm"
          variant="quiet"
          onClick={() => setAberto(false)}
          disabled={salvando}
        >
          Cancelar
        </Button>
      </div>
      {erro ? <p className="mt-2 text-xs text-danger">{erro}</p> : null}
    </div>
  );
}
