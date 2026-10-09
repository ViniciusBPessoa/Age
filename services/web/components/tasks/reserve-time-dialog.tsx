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

import { Button, Dialog, Field, Input, Spinner } from "@/components/ui";
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
          <Button variant="quiet" onClick={onClose}>
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
          <p className="mb-2 text-xs font-medium">
            Horários livres
            {window.deadline ? " antes do prazo" : " nos próximos 14 dias"}
          </p>

          {!validDuration ? null : slots.isLoading ? (
            <p className="text-xs text-[var(--muted)]">
              <Spinner /> procurando…
            </p>
          ) : slots.data && slots.data.slots.length > 0 ? (
            <ul className="space-y-1.5">
              {slots.data.slots.map((slot) => {
                const selected =
                  chosen?.start === slot.start && chosen?.end === slot.end;
                return (
                  <li key={`${slot.start}-${slot.end}`}>
                    <button
                      onClick={() => setChosen(slot)}
                      aria-pressed={selected}
                      className={[
                        "w-full rounded-md border px-3 py-2 text-left transition-colors",
                        selected
                          ? "border-[var(--accent)] bg-[var(--panel-2)]"
                          : "border-[var(--border)] hover:border-[var(--border-strong)]",
                      ].join(" ")}
                    >
                      <span className="block text-xs">
                        {range(slot.start, slot.end, timezone)}
                      </span>
                      {/* O motivo de cada slot vem do Scheduler (§5.4).
                          Mostrar "bloco livre mais longo antes do prazo"
                          é o que torna a escolha informada em vez de
                          aleatória. */}
                      {slot.reasons.length > 0 ? (
                        <span className="mt-0.5 block text-[11px] text-[var(--muted)]">
                          {slot.reasons.join(" · ")}
                        </span>
                      ) : null}
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-xs leading-relaxed text-[var(--warn)]">
              {slots.data?.hint ??
                "Nenhum horário livre nessa janela. Tente uma duração menor."}
            </p>
          )}
        </div>

        <p className="text-[11px] leading-relaxed text-[var(--muted)]">
          Isto monta uma proposta: o bloco e a tarefa ficam ligados, e você aprova os
          dois juntos. {duration(parsed || 0)} sai da sua agenda só depois disso.
        </p>
      </div>
    </Dialog>
  );
}
