/**
 * A tela de tarefas: visão própria, agrupável, com o tempo reservado.
 *
 * O painel do topo é a resposta de §5.9.5 — "o que vence e não tem
 * tempo reservado", com a estimativa somada só do que não tem. Ela vem
 * pronta de `GET /tasks/overview`, em `summary_text`, e não é remontada
 * aqui: a frase é do domínio, e o backend a monta com os números que
 * ele mesmo cruzou.
 */

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { PlanReview } from "@/components/plan-review/plan-review";
import { ReserveTimeDialog } from "@/components/tasks/reserve-time-dialog";
import { TaskRow } from "@/components/tasks/task-list";
import {
  Badge,
  Button,
  Dialog,
  Empty,
  Field,
  Input,
  LoadError,
  Panel,
  Select,
  Spinner,
} from "@/components/ui";
import { api } from "@/lib/api-client";
import { duration } from "@/lib/format";
import type {
  ApplyResult,
  Plan,
  Priority,
  Task,
  TaskListRef,
  TaskOverview,
} from "@/lib/types";

type GroupBy = "due" | "list" | "priority";

export function TasksScreen({ timezone }: { timezone: string }) {
  const queryClient = useQueryClient();
  const [groupBy, setGroupBy] = useState<GroupBy>("due");
  const [showDone, setShowDone] = useState(false);
  const [reserving, setReserving] = useState<Task | null>(null);
  const [creating, setCreating] = useState(false);
  // O plano que a reserva acabou de gerar, à espera de aprovação. Vive
  // aqui e não no diálogo porque o diálogo fecha: a revisão tem de
  // continuar na tela depois dele (§5.8 — nada entra sem aprovação, e
  // uma proposta que desaparece junto com o modal nunca é aprovada).
  const [pendingPlanId, setPendingPlanId] = useState<string | null>(null);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);

  const lists = useQuery({
    queryKey: ["task-lists"],
    queryFn: () => api.get<TaskListRef[]>("/tasks/lists"),
  });

  const tasks = useQuery({
    queryKey: ["tasks", showDone],
    // `status`, não `include_done`: o parâmetro da rota tem `alias`
    // (`Query(alias="status")`), e mandar o nome do argumento Python
    // seria ignorado em silêncio — a lista viria sempre só com as
    // abertas e o checkbox não faria nada.
    queryFn: () => api.get<Task[]>("/tasks", { status: showDone ? "all" : "open" }),
  });

  const overview = useQuery({
    queryKey: ["tasks", "overview"],
    // 30 dias, e não os 7 do default. O painel desta tela é a resposta
    // de §5.9.5 ("o que vence e não tem tempo reservado"), e com
    // janela de uma semana a prova do fim do mês não apareceria —
    // justamente o prazo que importa para quem estuda. A rota permite
    // até 366 porque a consulta é por `due_date` indexado.
    queryFn: () => api.get<TaskOverview>("/tasks/overview", { horizon_days: 30 }),
  });

  const plan = useQuery({
    queryKey: ["plan", pendingPlanId],
    queryFn: () => api.get<Plan>(`/plans/${pendingPlanId}`),
    enabled: pendingPlanId !== null,
  });

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ["tasks"] });
  }

  const toggle = useMutation({
    mutationFn: (task: Task) =>
      api.post(`/tasks/${task.id}/${task.status === "done" ? "reopen" : "complete"}`),
    onSuccess: refresh,
  });

  const remove = useMutation({
    mutationFn: (task: Task) => api.del(`/tasks/${task.id}`),
    onSuccess: refresh,
  });

  async function applyPlan(itemIds: string[], confirmBlocking: boolean) {
    if (!pendingPlanId) return;
    setApplying(true);
    setApplyError(null);
    try {
      const result = await api.post<ApplyResult>(`/plans/${pendingPlanId}/apply`, {
        item_ids: itemIds,
        confirm_blocking: confirmBlocking,
      });
      setPendingPlanId(null);
      refresh();
      // A agenda mudou também: o bloco reservado é um evento.
      void queryClient.invalidateQueries({ queryKey: ["events"] });
      if (result.manual_fix_needed.length > 0) setApplyError(result.message);
    } catch {
      setApplyError("Não consegui aplicar a reserva. Tente de novo.");
    } finally {
      setApplying(false);
    }
  }

  const groups = useMemo(
    () => groupTasks(tasks.data ?? [], groupBy, lists.data ?? [], timezone),
    [tasks.data, groupBy, lists.data, timezone],
  );

  return (
    <div className="min-h-dvh pb-14 md:pb-0">
      <header className="flex flex-wrap items-center gap-2 border-b border-[var(--border)] px-4 py-3">
        <h1 className="text-sm font-semibold">Tarefas</h1>
        {overview.data ? <Badge>{overview.data.total_open} abertas</Badge> : null}

        <div className="ml-auto flex items-center gap-2">
          <label htmlFor="group-by" className="sr-only">
            Agrupar por
          </label>
          <Select
            id="group-by"
            value={groupBy}
            onChange={(e) => setGroupBy(e.target.value as GroupBy)}
            className="w-auto py-1 text-xs"
          >
            <option value="due">por prazo</option>
            <option value="list">por lista</option>
            <option value="priority">por prioridade</option>
          </Select>
          <Button size="sm" variant="primary" onClick={() => setCreating(true)}>
            + Tarefa
          </Button>
        </div>
      </header>

      <div className="mx-auto max-w-3xl space-y-4 px-4 py-5">
        {/* §5.9.5: o cruzamento que as duas portas habilitam. */}
        {overview.data && overview.data.total_open > 0 ? (
          <Panel className="px-4 py-3">
            <p className="whitespace-pre-wrap text-sm leading-relaxed">
              {overview.data.summary_text}
            </p>
            {overview.data.estimated_minutes_unscheduled > 0 ? (
              <p className="mt-2 text-xs text-[var(--warn)]">
                {duration(overview.data.estimated_minutes_unscheduled)} de trabalho
                estimado sem horário marcado.
              </p>
            ) : null}
          </Panel>
        ) : null}

        {/* §5.8: a reserva vira proposta, e a proposta é revisada aqui
            mesmo — inclusive com os conflitos que a UI não calculou. */}
        {plan.data && plan.data.status === "proposed" ? (
          <PlanReview
            plan={plan.data}
            timezone={timezone}
            applying={applying}
            onApply={applyPlan}
            onReject={async () => {
              await api.post(`/plans/${pendingPlanId}/reject`).catch(() => undefined);
              setPendingPlanId(null);
            }}
          />
        ) : null}

        {applyError ? (
          <p role="alert" className="text-xs text-[var(--danger)]">
            {applyError}
          </p>
        ) : null}

        {tasks.isError ? (
          <LoadError
            message="Não consegui carregar sua lista."
            onRetry={() => void tasks.refetch()}
          />
        ) : tasks.isLoading ? (
          <p className="px-2 py-6 text-xs text-[var(--muted)]">
            <Spinner /> carregando…
          </p>
        ) : (tasks.data?.length ?? 0) === 0 ? (
          <Empty
            title="Nada na lista."
            action={
              <Button variant="primary" size="sm" onClick={() => setCreating(true)}>
                Criar a primeira
              </Button>
            }
          >
            Dá para pedir no chat também — &ldquo;preciso pagar o IPVA até dia 30&rdquo;
            vira tarefa com prazo.
          </Empty>
        ) : (
          groups.map((group) => (
            <section key={group.label}>
              <h2 className="px-1 pb-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--muted)]">
                {group.label}
              </h2>
              <ul className="space-y-2">
                {group.tasks.map((task) => (
                  <TaskRow
                    key={task.id}
                    task={task}
                    timezone={timezone}
                    busy={toggle.isPending || remove.isPending}
                    onToggle={(t) => toggle.mutate(t)}
                    onSchedule={setReserving}
                    onDelete={(t) => remove.mutate(t)}
                  />
                ))}
              </ul>
            </section>
          ))
        )}

        <label className="flex items-center gap-2 px-1 text-xs text-[var(--muted)]">
          <input
            type="checkbox"
            checked={showDone}
            onChange={(e) => setShowDone(e.target.checked)}
            className="accent-[var(--muted)]"
          />
          mostrar concluídas
        </label>
      </div>

      {reserving ? (
        <ReserveTimeDialog
          task={reserving}
          timezone={timezone}
          onClose={() => setReserving(null)}
          onDone={(planId) => {
            setReserving(null);
            setPendingPlanId(planId);
          }}
        />
      ) : null}

      <CreateTaskDialog
        open={creating}
        lists={lists.data ?? []}
        onClose={() => setCreating(false)}
        onDone={() => {
          setCreating(false);
          refresh();
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------
interface Group {
  label: string;
  tasks: Task[];
}

function groupTasks(
  tasks: Task[],
  groupBy: GroupBy,
  lists: TaskListRef[],
  timezone: string,
): Group[] {
  if (groupBy === "list") {
    const byList = new Map<string, Task[]>();
    for (const task of tasks) {
      const key = lists.find((l) => l.id === task.list_id)?.name ?? "Outra lista";
      byList.set(key, [...(byList.get(key) ?? []), task]);
    }
    return [...byList.entries()].map(([label, items]) => ({ label, tasks: items }));
  }

  if (groupBy === "priority") {
    const order: (Priority | "none")[] = ["urgent", "high", "normal", "low", "none"];
    const labels: Record<string, string> = {
      urgent: "Urgente",
      high: "Alta",
      normal: "Normal",
      low: "Baixa",
      none: "Sem prioridade",
    };
    return order
      .map((key) => ({
        label: labels[key],
        tasks: tasks.filter((t) => (t.priority ?? "none") === key),
      }))
      .filter((group) => group.tasks.length > 0);
  }

  // Por prazo: a ordem de §8.1 — atrasadas, vencendo, depois o resto.
  // `is_overdue` vem do backend; agrupar por ele aqui é só leitura.
  const overdue = tasks.filter((t) => t.is_overdue && t.status !== "done");
  const withDue = tasks.filter(
    (t) => !t.is_overdue && (t.due_at ?? t.due_date) && t.status !== "done",
  );
  const noDue = tasks.filter((t) => !(t.due_at ?? t.due_date) && t.status !== "done");
  const done = tasks.filter((t) => t.status === "done");

  return [
    { label: "Atrasadas", tasks: overdue },
    { label: "Com prazo", tasks: withDue },
    { label: "Sem prazo", tasks: noDue },
    { label: "Concluídas", tasks: done },
  ].filter((group) => group.tasks.length > 0);
}

// ---------------------------------------------------------------
function CreateTaskDialog({
  open,
  lists,
  onClose,
  onDone,
}: {
  open: boolean;
  lists: TaskListRef[];
  onClose: () => void;
  onDone: () => void;
}) {
  const [title, setTitle] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [priority, setPriority] = useState<Priority | "">("");
  const [estimated, setEstimated] = useState("");
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () =>
      api.post<Task>("/tasks", {
        title: title.trim(),
        due_date: dueDate || undefined,
        priority: priority || undefined,
        estimated_min: estimated ? Number(estimated) : undefined,
        list_id: lists.find((l) => l.is_default)?.id,
      }),
    onSuccess: () => {
      setTitle("");
      setDueDate("");
      setPriority("");
      setEstimated("");
      setError(null);
      onDone();
    },
    onError: () => setError("Não consegui criar. Tente de novo."),
  });

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Nova tarefa"
      footer={
        <>
          <Button variant="quiet" onClick={onClose}>
            Cancelar
          </Button>
          <Button
            variant="primary"
            loading={create.isPending}
            disabled={title.trim().length === 0}
            onClick={() => create.mutate()}
          >
            Criar
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="O quê" htmlFor="t-title" error={error}>
          <Input
            id="t-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={500}
            autoFocus
          />
        </Field>

        <div className="grid grid-cols-2 gap-3">
          <Field label="Prazo" htmlFor="t-due">
            <Input
              id="t-due"
              type="date"
              value={dueDate}
              onChange={(e) => setDueDate(e.target.value)}
            />
          </Field>

          <Field label="Prioridade" htmlFor="t-prio">
            <Select
              id="t-prio"
              value={priority}
              onChange={(e) => setPriority(e.target.value as Priority | "")}
            >
              <option value="">normal</option>
              <option value="low">baixa</option>
              <option value="high">alta</option>
              <option value="urgent">urgente</option>
            </Select>
          </Field>
        </div>

        <Field
          label="Quanto tempo leva"
          htmlFor="t-est"
          hint="Em minutos. É o que permite responder se cabe na semana."
        >
          <Input
            id="t-est"
            type="number"
            min={1}
            max={1440}
            value={estimated}
            onChange={(e) => setEstimated(e.target.value)}
            placeholder="90"
          />
        </Field>
      </div>
    </Dialog>
  );
}
