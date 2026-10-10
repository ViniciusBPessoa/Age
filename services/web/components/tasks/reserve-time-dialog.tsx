/**
 * O diálogo do botão "reservar tempo" (§8.1).
 *
 * Ele não pede ao usuário que digite um horário: pede a duração e
 * mostra os horários que o `Scheduler` achou, com o motivo de cada um
 * (§5.3, §5.4). A diferença importa — quem digita horário à mão escolhe
 * sem saber que vai colidir com a aula das 16h, e descobre no cartão de
 * conflito depois.
 *
 * `GET /calendar/free-slots` é determinístico e não passa pelo LLM. A
 * própria rota registra que existe para isto: "o botão 'reservar tempo'
 * não passa pelo chat".
 *
 * O resultado é um plano `proposed`, não um fato. §5.8 vale para o
 * clique como vale para a conversa: nada entra na agenda sem aprovação.
 */

"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { addDays } from "date-fns";
import { useMemo, useState } from "react";

import {
  Button,
  Dialog,
  Field,
  IconCheck,
  IconClock,
  Input,
  Spinner,
  cx,
} from "@/components/ui";
import { RequestFailed, api } from "@/lib/api-client";
import { duration, range } from "@/lib/format";
import type { Slot, Task } from "@/lib/types";

export function ReserveTimeDialog({
  task,
  timezone,
  onClose,
  onDone,
}: {
  task: Task;
  timezone: string;
  onClose: () => void;
  onDone: (planId: string) => void;
}) {
  // A estimativa da tarefa é o palpite inicial — é o número que a
  // pessoa já deu, e repetir a pergunta seria pedir duas vezes.
  const [minutes, setMinutes] = useState(String(task.estimated_min ?? 60));
  const [chosen, setChosen] = useState<Slot | null>(null);
  const [error, setError] = useState<string | null>(null);

  const window = useMemo(() => {
    const now = new Date();
    // Até o prazo, se houver; 14 dias se não. Buscar além do prazo
    // ofereceria horário que não serve.
    const deadline = task.due_at
      ? new Date(task.due_at)
      : task.due_date
        ? new Date(`${task.due_date}T23:59:59`)
        : null;
    const end = deadline && deadline > now ? deadline : addDays(now, 14);
    return { start: now.toISOString(), end: end.toISOString(), deadline };
  }, [task.due_at, task.due_date]);

  const parsed = Number(minutes);
  const validDuration = Number.isFinite(parsed) && parsed >= 5 && parsed <= 600;

  const slots = useQuery({
    queryKey: ["free-slots", task.id, minutes],
    queryFn: () =>
      api.get<{ slots: Slot[]; hint: string | null }>("/calendar/free-slots", {
        start: window.start,
        end: window.end,
        duration_min: parsed,
        deadline: window.deadline?.toISOString(),
        count: 8,
      }),
    enabled: validDuration,
  });

  const reserve = useMutation({
    mutationFn: (slot: Slot) =>
      api.post<{ plan_id: string; message: string }>(`/tasks/${task.id}/schedule`, {
        start: slot.start,
        end: slot.end,
        title: task.title,
      }),
    onSuccess: (result) => onDone(result.plan_id),
    onError: (err) =>
      setError(
        err instanceof RequestFailed ? err.message : "Não consegui montar a reserva.",
      ),
  });

  return (
    <Dialog
      open
      onClose={onClose}
      title={`Reservar tempo · ${task.title}`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button
            variant="primary"
            loading={reserve.isPending}
            disabled={chosen === null}
            onClick={() => chosen && reserve.mutate(chosen)}
          >
            Reservar
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field
          label="Quanto tempo"
          htmlFor="res-min"
          hint="Em minutos. Os horários abaixo recalculam."
          error={validDuration ? error : "Use entre 5 e 600 minutos."}
        >
          <Input
            id="res-min"
            type="number"
            min={5}
            max={600}
            value={minutes}
            onChange={(e) => {
              setMinutes(e.target.value);
              setChosen(null);
            }}
          />
        </Field>

        <div>
          <p className="eyebrow mb-2.5">
            Horários livres
            {window.deadline ? " antes do prazo" : " nos próximos 14 dias"}
          </p>

          {!validDuration ? null : slots.isLoading ? (
            <div className="space-y-1.5" aria-hidden>
              {Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className="skeleton h-14 rounded-xl" />
              ))}
            </div>
          ) : slots.data && slots.data.slots.length > 0 ? (
            <ul className="max-h-64 space-y-1.5 overflow-y-auto pr-0.5">
              {slots.data.slots.map((slot) => {
                const selected =
                  chosen?.start === slot.start && chosen?.end === slot.end;
                return (
                  <li key={`${slot.start}-${slot.end}`}>
                    {/*
                     * O cartão de horário é a decisão desta tela, e
                     * agora parece uma escolha: 44px de altura, o
                     * horário em 13px com números tabulares, e a marca
                     * de selecionado à direita em vez de só uma borda
                     * azul de 1px.
                     */}
                    <button
                      onClick={() => setChosen(slot)}
                      aria-pressed={selected}
                      className={cx(
                        "flex w-full items-center gap-3 rounded-xl border px-3.5 py-2.5 text-left",
                        "transition-all duration-150",
                        selected
                          ? "border-brand bg-brand/10 ring-[3px] ring-brand/15"
                          : "border-line bg-surface-2 hover:border-line-strong hover:bg-surface-3",
                      )}
                    >
                      <IconClock
                        className={cx(
                          "h-4 w-4 shrink-0",
                          selected ? "text-brand" : "text-muted",
                        )}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="tnum block text-sm font-medium">
                          {range(slot.start, slot.end, timezone)}
                        </span>
                        {/* O motivo de cada slot vem do Scheduler
                            (§5.4). Mostrar "bloco livre mais longo
                            antes do prazo" é o que torna a escolha
                            informada em vez de aleatória. */}
                        {slot.reasons.length > 0 ? (
                          <span className="mt-0.5 block text-xs leading-relaxed text-muted">
                            {slot.reasons.join(" · ")}
                          </span>
                        ) : null}
                      </span>
                      {selected ? (
                        <span
                          aria-hidden
                          className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-brand text-brand-ink"
                        >
                          <IconCheck className="h-3 w-3" strokeWidth={3} />
                        </span>
                      ) : null}
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="rounded-xl bg-warn/10 px-3.5 py-3 text-xs leading-relaxed text-warn">
              {slots.data?.hint ??
                "Nenhum horário livre nessa janela. Tente uma duração menor."}
            </p>
          )}
        </div>

        <p className="rounded-xl bg-surface-2 px-3.5 py-3 text-xs leading-relaxed text-muted">
          Isto monta uma <strong className="font-medium text-ink-2">proposta</strong>: o
          bloco e a tarefa ficam ligados, e você aprova os dois juntos.{" "}
          {duration(parsed || 0)} sai da sua agenda só depois disso.
        </p>
      </div>
    </Dialog>
  );
}
