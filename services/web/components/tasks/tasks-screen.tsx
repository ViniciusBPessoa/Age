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
  Button,
  Dialog,
  Empty,
  Field,
  IconAlert,
  IconPlus,
  IconTasks,
  Input,
  LoadError,
  PageHeader,
  Select,
  SkeletonList,
  cx,
} from "@/components/ui";
import { RequestFailed, api } from "@/lib/api-client";
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
    <div className="min-h-dvh pb-16 md:pb-0">
      <PageHeader
        title="Tarefas"
        subtitle={
          overview.data
            ? `${overview.data.total_open} ${
                overview.data.total_open === 1 ? "aberta" : "abertas"
              }`
            : undefined
        }
        action={
          <div className="flex items-center gap-2">
            <label htmlFor="group-by" className="sr-only">
              Agrupar por
            </label>
            <Select
              bare
              id="group-by"
              value={groupBy}
              onChange={(e) => setGroupBy(e.target.value as GroupBy)}
            >
              <option value="due">por prazo</option>
              <option value="list">por lista</option>
              <option value="priority">por prioridade</option>
            </Select>
            <Button variant="primary" onClick={() => setCreating(true)}>
              <IconPlus />
              Tarefa
            </Button>
          </div>
        }
      />

      <div className="mx-auto max-w-3xl space-y-5 px-4 py-6 md:px-7">
        {/* §5.9.5: o cruzamento que as duas portas habilitam. */}
        {overview.data && overview.data.total_open > 0 ? (
          <div className="card overflow-hidden">
            {/*
             * Numeros, nao o paragrafo pronto.
             *
             * Aqui ficava o `summary_text` inteiro do backend, em
             * `whitespace-pre-wrap`: um bloco de texto corrido que
             * REPETIA, linha por linha, a lista de tarefas logo
             * abaixo, e no lugar mais nobre da tela. Visto no
             * navegador, era a coisa menos legivel da pagina.
             *
             * A frase continua util para a CONVERSA, onde o Age fala.
             * Numa tela que ja mostra as tarefas, o que falta e o
             * agregado: quantas atrasadas, quantas vencendo, quanto
             * tempo sem horario. Isso se le de relance.
             */}
            <div className="flex flex-wrap items-center gap-x-6 gap-y-3 px-4 py-4 md:px-5">
              <span
                aria-hidden
                className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-brand/12 text-brand"
              >
                <IconTasks className="h-[18px] w-[18px]" />
              </span>
              {overview.data.overdue.length > 0 ? (
                <Numero
                  valor={overview.data.overdue.length}
                  rotulo="atrasada"
                  tom="danger"
                />
              ) : null}
              {overview.data.due_soon.length > 0 ? (
                <Numero
                  valor={overview.data.due_soon.length}
                  rotulo="vence em breve"
                  tom="warn"
                />
              ) : null}
              {overview.data.without_due_count > 0 ? (
                <Numero
                  valor={overview.data.without_due_count}
                  rotulo="sem prazo"
                  tom="muted"
                />
              ) : null}
            </div>
            {overview.data.estimated_minutes_unscheduled > 0 ? (
              <p className="flex items-center gap-2 border-t border-warn/20 bg-warn/8 px-4 py-2.5 text-xs font-medium text-warn md:px-5">
                <IconAlert className="h-3.5 w-3.5 shrink-0" />
                {duration(overview.data.estimated_minutes_unscheduled)} de trabalho
                estimado sem horário marcado.
              </p>
            ) : null}
          </div>
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
              /*
               * O erro aparece, e `setPendingPlanId(null)` só acontece
               * se o servidor confirmou.
               *
               * Havia um `.catch(() => undefined)` aqui, o mesmo
               * padrão que escondeu oito respostas 404 na tela de
               * conversa. Com ele, um `reject` que falhasse parecia ter
               * dado certo: o cartão saía da tela e a proposta
               * continuava pendente no banco, reaparecendo depois sem
               * explicação.
               */
              try {
                await api.post(`/plans/${plan.data.id}/reject`);
                setPendingPlanId(null);
              } catch (err) {
                setApplyError(
                  err instanceof RequestFailed
                    ? err.message
                    : "Não consegui descartar a reserva. Tente de novo.",
                );
              }
            }}
          />
        ) : null}

        {applyError ? (
          <p
            role="alert"
            className="flex items-start gap-2 rounded-xl bg-danger/10 px-3.5 py-2.5 text-xs leading-relaxed text-danger ring-1 ring-inset ring-danger/25"
          >
            <IconAlert className="mt-px h-3.5 w-3.5 shrink-0" />
            {applyError}
          </p>
        ) : null}

        {tasks.isError ? (
          <LoadError
            message="Não consegui carregar sua lista."
            onRetry={() => void tasks.refetch()}
          />
        ) : tasks.isLoading ? (
          <SkeletonList rows={4} />
        ) : (tasks.data?.length ?? 0) === 0 ? (
          <Empty
            title="Nada na lista."
            icon={<IconTasks className="h-5 w-5" />}
            action={
              <Button variant="primary" onClick={() => setCreating(true)}>
                <IconPlus />
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
              <h2 className="eyebrow flex items-center gap-2 px-1 pb-2">
                {group.label}
                <span className="tnum text-faint">{group.tasks.length}</span>
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

        <label className="flex w-fit cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-xs text-muted transition-colors hover:bg-surface-2 hover:text-ink">
          <input
            type="checkbox"
            checked={showDone}
            onChange={(e) => setShowDone(e.target.checked)}
            className="h-3.5 w-3.5 cursor-pointer accent-brand"
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
          <Button variant="ghost" onClick={onClose}>
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

/**
 * Um numero grande com o rotulo embaixo.
 *
 * A escala tipografica do projeto vai ate `text-4xl` e quase nada
 * passava de `text-base`: numero que importa merece corpo, senao a
 * tela inteira tem o mesmo peso e nada salta.
 */
function Numero({
  valor,
  rotulo,
  tom,
}: {
  valor: number;
  rotulo: string;
  tom: "danger" | "warn" | "muted";
}) {
  const cor =
    tom === "danger" ? "text-danger" : tom === "warn" ? "text-warn" : "text-ink-2";
  return (
    <span className="flex flex-col">
      <span className={cx("text-2xl font-semibold tabular-nums", cor)}>{valor}</span>
      <span className="text-2xs uppercase tracking-[0.08em] text-muted">
        {rotulo}
        {valor === 1 || rotulo.includes("vence") ? "" : "s"}
      </span>
    </span>
  );
}
