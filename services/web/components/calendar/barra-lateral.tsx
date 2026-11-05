/**
 * A barra lateral da agenda: criar, pular de dia, e o que falta encaixar.
 *
 * A composição segue o Google Agenda e o Notion Calendar, que chegaram ao
 * mesmo desenho por caminhos diferentes: botão de criar no topo, mês em
 * miniatura, e embaixo o que pode virar bloco de tempo. No Age, isso é a
 * lista de tarefas sem horário reservado (§5.9.5): arraste uma para a
 * grade e ela vira proposta de reserva, no lugar onde foi solta.
 */

"use client";

import { useQuery } from "@tanstack/react-query";
import { format } from "date-fns";
import { ptBR } from "date-fns/locale";

import { MiniMes } from "@/components/calendar/mini-mes";
import { Button, IconClock, IconLock, IconPlus, IconTasks, cx } from "@/components/ui";
import { api } from "@/lib/api-client";
import { ATALHOS_DA_AGENDA } from "@/lib/atalhos";
import type { Task } from "@/lib/types";

export interface TarefaArrastada {
  id: string;
  titulo: string;
  minutos: number;
}

function prazo(t: Task): string | null {
  const iso = t.due_at ?? t.due_date;
  if (!iso) return null;
  const d = new Date(t.due_at ?? `${t.due_date}T12:00:00`);
  return format(d, "d MMM", { locale: ptBR });
}

export function BarraLateral({
  cursor,
  timezone,
  onEscolherDia,
  onNovo,
  onArrastarTarefa,
  onAjuda,
}: {
  cursor: Date;
  timezone: string;
  onEscolherDia: (d: Date) => void;
  onNovo: () => void;
  onArrastarTarefa: (t: TarefaArrastada | null) => void;
  onAjuda: () => void;
}) {
  const tarefas = useQuery({
    queryKey: ["tasks", "abertas"],
    queryFn: () => api.get<Task[]>("/tasks"),
    staleTime: 30_000,
  });

  // Resposta fora do formato (proxy, versão antiga da API) não derruba
  // a agenda inteira: a barra só fica sem a lista.
  const semHorario = (Array.isArray(tarefas.data) ? tarefas.data : [])
    .filter((t) => t.status === "open" && t.reserved.length === 0)
    .sort((a, b) => {
      const pa = a.due_at ?? a.due_date ?? "9999";
      const pb = b.due_at ?? b.due_date ?? "9999";
      return pa.localeCompare(pb);
    })
    .slice(0, 8);

  return (
    <aside
      aria-label="Ferramentas da agenda"
      className="hidden w-64 shrink-0 flex-col gap-5 overflow-y-auto border-r border-line px-4 py-4 lg:flex"
    >
      <Button
        variant="primary"
        onClick={onNovo}
        className="w-full justify-center shadow-brand"
      >
        <IconPlus className="h-4 w-4" />
        Novo compromisso
      </Button>

      <MiniMes selecionado={cursor} onEscolher={onEscolherDia} timezone={timezone} />

      <section aria-labelledby="sem-horario" className="space-y-2">
        <h2
          id="sem-horario"
          className="flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wider text-muted"
        >
          <IconTasks className="h-3.5 w-3.5" />
          Sem horário
        </h2>
        {tarefas.isLoading ? (
          <p className="text-xs text-muted">Carregando tarefas…</p>
        ) : semHorario.length === 0 ? (
          <p className="text-xs leading-relaxed text-muted">
            Nenhuma tarefa esperando tempo na agenda.
          </p>
        ) : (
          <>
            <p className="text-2xs leading-relaxed text-muted">
              Arraste para a grade para reservar o tempo.
            </p>
            <ul className="space-y-1.5">
              {semHorario.map((t) => {
                const quando = prazo(t);
                return (
                  <li key={t.id}>
                    <div
                      draggable
                      onDragStart={(e) => {
                        e.dataTransfer.effectAllowed = "move";
                        e.dataTransfer.setData("text/plain", t.title);
                        onArrastarTarefa({
                          id: t.id,
                          titulo: t.title,
                          minutos: t.estimated_min ?? 60,
                        });
                      }}
                      onDragEnd={() => setTimeout(() => onArrastarTarefa(null), 0)}
                      className={cx(
                        "group cursor-grab rounded-lg border border-line bg-surface px-2.5 py-2 text-xs",
                        "transition-colors hover:border-brand/50 hover:bg-surface-2 active:cursor-grabbing",
                      )}
                      title="Arraste para a agenda"
                    >
                      <p className="truncate font-medium text-ink">{t.title}</p>
                      <p className="mt-0.5 flex items-center gap-2 text-2xs text-muted">
                        <span className="flex items-center gap-1">
                          <IconClock className="h-3 w-3" />
                          {t.estimated_min ?? 60} min
                        </span>
                        {quando ? (
                          <span
                            className={cx(t.is_overdue && "font-semibold text-danger")}
                          >
                            {t.is_overdue ? "venceu " : "até "}
                            {quando}
                          </span>
                        ) : null}
                      </p>
                    </div>
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </section>

      <section aria-labelledby="legenda" className="space-y-2">
        <h2
          id="legenda"
          className="text-2xs font-semibold uppercase tracking-wider text-muted"
        >
          Legenda
        </h2>
        <ul className="age-timeline space-y-1.5 text-xs text-muted">
          <li className="flex items-center gap-2">
            <span aria-hidden className="age-ev age-ev-flexible h-3 w-6 rounded" />
            flexível: o Age pode remarcar
          </li>
          <li className="flex items-center gap-2">
            <span aria-hidden className="age-ev age-ev-semi h-3 w-6 rounded" />
            semi-flexível: move avisando
          </li>
          <li className="flex items-center gap-2">
            <span aria-hidden className="age-ev age-ev-fixed h-3 w-6 rounded" />
            fixo <IconLock className="h-3 w-3" />
          </li>
          <li className="flex items-center gap-2">
            <span
              aria-hidden
              className="age-ev age-ev-proposed age-hatch h-3 w-6 rounded"
            />
            proposta esperando você
          </li>
        </ul>
      </section>

      <button
        type="button"
        onClick={onAjuda}
        className="mt-auto rounded-lg px-2 py-1.5 text-left text-2xs text-muted transition-colors hover:bg-surface-2 hover:text-ink"
      >
        Atalhos:{" "}
        {ATALHOS_DA_AGENDA.slice(0, 3)
          .map((a) => a.teclas)
          .join(" · ")}{" "}
        · <kbd className="font-mono">?</kbd> para todos
      </button>
    </aside>
  );
}
