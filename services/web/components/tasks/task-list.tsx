/**
 * Lista de tarefas (§8.1).
 *
 * "Cada tarefa mostra se **tem tempo reservado** (§5.7.1) — é a
 * informação que diferencia o Age de um to-do comum":
 *
 *   ☐  Relatório do TCC              vence 20/10
 *      🕐 3h reservadas · qui 14:00–17:00
 *   ☐  Revisar capítulo 2            vence 10/10  ⚠
 *      sem tempo reservado            [reservar tempo]
 *
 * O `⚠` em tarefa com prazo próximo e sem tempo reservado "é o sinal
 * que o produto existe para dar". Sem ele a lista é uma lista, e o
 * cruzamento das duas portas (§3.2 e §3.3) não aparece em lugar nenhum
 * da tela.
 */

"use client";

import { Badge, Button, Panel } from "@/components/ui";
import { daysUntil, dueLabel, duration, range } from "@/lib/format";
import type { Priority, Task } from "@/lib/types";

const PRIORITY_TONE: Record<Priority, "neutral" | "accent" | "warn" | "danger"> = {
  low: "neutral",
  normal: "neutral",
  high: "warn",
  urgent: "danger",
};

const PRIORITY_LABEL: Record<Priority, string> = {
  low: "baixa",
  normal: "normal",
  high: "alta",
  urgent: "urgente",
};

export function TaskRow({
  task,
  timezone,
  busy,
  onToggle,
  onSchedule,
  onDelete,
}: {
  task: Task;
  timezone: string;
  busy?: boolean;
  onToggle: (task: Task) => void;
  onSchedule: (task: Task) => void;
  onDelete: (task: Task) => void;
}) {
  const done = task.status === "done";
  const hasReserved = task.reserved.length > 0;

  /**
   * O `⚠` de §8.1: prazo próximo (ou passado) e nenhum tempo reservado.
   *
   * `is_overdue` vem do backend porque depende do fuso e do "prazo só
   * com data vale até o fim do dia" — regra de domínio, não de
   * apresentação (§9.5).
   */
  const needsTime =
    !done && !hasReserved && (task.is_overdue || isSoon(task, timezone));

  return (
    <Panel as="li" className="px-4 py-3">
      <div className="flex items-start gap-3">
        <input
          type="checkbox"
          checked={done}
          disabled={busy}
          onChange={() => onToggle(task)}
          aria-label={done ? `Reabrir ${task.title}` : `Concluir ${task.title}`}
          className="mt-1 accent-[var(--ok)]"
        />

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={`text-sm ${done ? "text-[var(--muted)] line-through" : ""}`}
            >
              {task.title}
            </span>

            {task.priority && task.priority !== "normal" ? (
              <Badge tone={PRIORITY_TONE[task.priority]}>
                {PRIORITY_LABEL[task.priority]}
              </Badge>
            ) : null}

            {task.labels.map((label) => (
              <Badge key={label}>{label}</Badge>
            ))}

            {needsTime ? (
              <span
                className="text-[var(--warn)]"
                title="prazo chegando e sem tempo reservado"
                aria-label="prazo chegando e sem tempo reservado"
              >
                ⚠
              </span>
            ) : null}
          </div>

          <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--muted)]">
            {(task.due_at ?? task.due_date) ? (
              <span className={task.is_overdue && !done ? "text-[var(--danger)]" : ""}>
                {dueLabel(task.due_at ?? `${task.due_date}T23:59:59Z`, timezone)}
              </span>
            ) : (
              <span>sem prazo</span>
            )}

            {task.estimated_min ? <span>{duration(task.estimated_min)}</span> : null}
          </div>

          {/* A linha que é o ponto desta tela. */}
          <div className="mt-1.5">
            {hasReserved ? (
              <div className="space-y-0.5">
                {task.reserved.map((block) => (
                  <p key={block.event_ref_id} className="text-xs text-[var(--accent)]">
                    🕐 {duration(block.minutes)} reservad
                    {block.minutes === 60 ? "a" : "as"} ·{" "}
                    {range(block.start, block.end, timezone)}
                  </p>
                ))}
              </div>
            ) : done ? null : (
              <div className="flex items-center gap-2">
                <span className="text-xs text-[var(--muted)]">sem tempo reservado</span>
                <Button size="sm" variant="ghost" onClick={() => onSchedule(task)}>
                  reservar tempo
                </Button>
              </div>
            )}
          </div>

          {/* §3.3.2: a degradação aparece NO ITEM, não só na tela de
              conexão — "o usuário precisa saber ao olhar a tarefa". */}
          {task.dropped_fields.length > 0 ? (
            <p className="mt-1.5 text-[11px] leading-relaxed text-[var(--warn)]">
              ⚠ O {task.provider_name ?? "provedor"} não guarda{" "}
              {task.dropped_fields.join(", ")} — isso fica só aqui no Age.
            </p>
          ) : null}
        </div>

        <button
          onClick={() => onDelete(task)}
          aria-label={`Apagar ${task.title}`}
          className="rounded px-1.5 py-0.5 text-xs text-[var(--muted)] hover:bg-[var(--panel-2)] hover:text-[var(--danger)]"
        >
          ✕
        </button>
      </div>
    </Panel>
  );
}

/**
 * Prazo dentro de 7 dias — o mesmo horizonte que `/tasks/overview` usa.
 *
 * Calcula sobre data, e não sobre o texto de `dueLabel`. A primeira
 * versão fazia regex no rótulo ("vence em \d+ dias"), o que acopla uma
 * decisão a uma string de interface: mudar "vence em 3 dias" para "em 3
 * dias" apagaria o `⚠` de toda a lista sem erro nenhum, e o aviso que
 * §8.1 chama de "o sinal que o produto existe para dar" desapareceria
 * em silêncio.
 */
function isSoon(task: Task, timezone: string): boolean {
  const iso = task.due_at ?? (task.due_date ? `${task.due_date}T23:59:59Z` : null);
  if (!iso) return false;
  return daysUntil(iso, timezone) <= 7;
}
