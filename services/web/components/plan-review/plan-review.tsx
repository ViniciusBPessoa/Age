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
 */

"use client";

import { useMemo, useState } from "react";

import { Badge, Button, Panel } from "@/components/ui";
import { duration, range } from "@/lib/format";
import type { Plan, PlanItem, PlanOp } from "@/lib/types";

const OP_LABEL: Record<PlanOp, { icon: string; label: string }> = {
  create: { icon: "✦", label: "CRIAR" },
  move: { icon: "⇄", label: "MOVER" },
  resize: { icon: "⇔", label: "AJUSTAR" },
  delete: { icon: "✕", label: "REMOVER" },
  complete: { icon: "✓", label: "CONCLUIR" },
  reorder: { icon: "↕", label: "REORDENAR" },
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
}: {
  plan: Plan;
  timezone: string;
  applying?: boolean;
  onApply: (itemIds: string[], confirmBlocking: boolean) => void;
  onReject: () => void;
}) {
  // Tudo marcado por padrão: o plano é uma proposta coerente, e obrigar
  // a marcar seis caixas para aceitar o que foi pedido é atrito contra
  // o caso comum. Desmarcar é a exceção, e exceção é que deve custar o
  // clique.
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(plan.items.filter((i) => i.status === "pending").map((i) => i.id)),
  );
  const [confirmBlocking, setConfirmBlocking] = useState(false);

  const pending = plan.items.filter((item) => item.status === "pending");

  /**
   * O par ligado se aprova junto (§5.7.1).
   *
   * "Reservar tempo para uma tarefa cria DOIS itens ligados; aprovado,
   * aplicado e revertido como unidade." Deixar marcar só o evento
   * produziria um bloco de tempo sem tarefa nenhuma — o oposto do que
   * a pessoa pediu.
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
  const canApply =
    selected.size > 0 && (!needsConfirm || confirmBlocking) && !plan.is_expired;

  if (plan.is_expired) {
    return (
      <Panel className="p-4">
        <p className="text-sm text-[var(--muted)]">
          Esta proposta expirou. A agenda pode ter mudado desde que ela foi feita, então
          aplicá-la agora poderia criar conflito — peça de novo e eu recalculo.
        </p>
      </Panel>
    );
  }

  return (
    <Panel className="overflow-hidden">
      <div className="border-b border-[var(--border)] px-4 py-3">
        <h3 className="text-sm font-semibold">{plan.summary}</h3>
        {plan.origin_note ? (
          <p className="mt-0.5 text-xs text-[var(--muted)]">{plan.origin_note}</p>
        ) : null}
      </div>

      <ul className="divide-y divide-[var(--border)]">
        {pending.map((item) => (
          <ItemCard
            key={item.id}
            item={item}
            timezone={timezone}
            checked={selected.has(item.id)}
            linked={item.links.length > 0}
            onToggle={() => toggle(item)}
          />
        ))}
      </ul>

      {needsConfirm ? (
        <div className="border-t border-[var(--border)] bg-[var(--panel-2)] px-4 py-3">
          <label className="flex cursor-pointer items-start gap-2 text-xs leading-relaxed">
            <input
              type="checkbox"
              checked={confirmBlocking}
              onChange={(e) => setConfirmBlocking(e.target.checked)}
              className="mt-0.5 accent-[var(--warn)]"
            />
            <span>
              <strong className="text-[var(--warn)]">
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

      <div className="flex flex-wrap items-center gap-2 border-t border-[var(--border)] px-4 py-3">
        <Button
          variant="primary"
          loading={applying}
          disabled={!canApply}
          onClick={() => onApply([...selected], confirmBlocking)}
        >
          {applyLabel(selected.size, pending.length)}
        </Button>
        <Button variant="quiet" onClick={onReject} disabled={applying}>
          Rejeitar tudo
        </Button>
        {selected.size < pending.length && selected.size > 0 ? (
          <span className="text-xs text-[var(--muted)]">
            {pending.length - selected.size} fora — dá para pedir um recálculo depois.
          </span>
        ) : null}
      </div>
    </Panel>
  );
}

function ItemCard({
  item,
  timezone,
  checked,
  linked,
  onToggle,
}: {
  item: PlanItem;
  timezone: string;
  checked: boolean;
  linked: boolean;
  onToggle: () => void;
}) {
  const op = OP_LABEL[item.op] ?? { icon: "•", label: item.op.toUpperCase() };
  const before = payload(item.before);
  const after = payload(item.after);
  const isTask = item.target === "task";
  const subject = after ?? before;

  return (
    <li className="px-4 py-3">
      <label className="flex cursor-pointer items-start gap-3">
        <input
          type="checkbox"
          checked={checked}
          onChange={onToggle}
          className="mt-1 accent-[var(--accent)]"
          aria-describedby={`reason-${item.id}`}
        />

        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2">
            <span
              aria-hidden
              className="text-xs"
              style={{
                color: isTask ? "var(--accent)" : "var(--proposed)",
              }}
            >
              {op.icon}
            </span>
            <span className="text-[10px] font-semibold tracking-wide text-[var(--muted)]">
              {op.label}
            </span>
            <span className="text-sm font-medium">
              {subject?.title ?? "(sem título)"}
            </span>
            {isTask ? <Badge>📋 lista</Badge> : null}
            {linked ? (
              // §5.7.1: o par se aprova junto, e dizer isso evita a
              // surpresa de marcar um e ver o outro mudar.
              <Badge tone="accent">par ligado</Badge>
            ) : null}
          </span>

          {/* O quando. Tarefa mostra prazo; evento mostra o intervalo —
              são coisas diferentes, e §5.7 insiste nisso: prazo não
              ocupa a agenda. */}
          <span className="mt-1 block text-xs text-[var(--muted)]">
            {item.op === "move" && before?.start && after?.start ? (
              <>
                {range(before.start, before.end ?? before.start, timezone)}
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
            {subject?.estimated_min ? ` · ${duration(subject.estimated_min)}` : null}
          </span>

          {/* O motivo. §5.7 exige que todo item carregue um — "porque o
              assistente disse" não é justificativa. */}
          <span
            id={`reason-${item.id}`}
            className="mt-1 block text-xs leading-relaxed text-[var(--muted)]"
          >
            {item.reason}
          </span>

          {item.conflicts.length > 0 ? (
            <span className="mt-1.5 block space-y-1">
              {item.conflicts.map((conflict, index) => (
                <span
                  key={index}
                  className={`block text-xs ${
                    conflict.severity === "blocking"
                      ? "text-[var(--danger)]"
                      : "text-[var(--warn)]"
                  }`}
                >
                  ⚠ {conflict.message}
                </span>
              ))}
            </span>
          ) : null}

          {/* Flexibilidade semi do que está sendo movido: §8.1 mostra
              "⚠ evento semi-flexível" porque mover algo que a pessoa
              marcou como quase-fixo merece aviso. */}
          {item.op === "move" && before?.flexibility === "semi_flexible" ? (
            <span className="mt-1 block text-xs text-[var(--warn)]">
              ⚠ evento semi-flexível
            </span>
          ) : null}
        </span>
      </label>
    </li>
  );
}
