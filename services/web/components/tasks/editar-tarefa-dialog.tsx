/**
 * Editar uma tarefa depois de criada (§8.1).
 *
 * O relato: *"não existe nenhum botão para fazer edição do meu to-do
 * uma vez que ele foi criado"*. A rota `PATCH /tasks/{id}` existia
 * desde o M4 e nenhuma tela a chamava — o único caminho para corrigir
 * um título era apagar e refazer, perdendo o tempo já reservado.
 *
 * Campos ausentes no corpo preservam o valor no servidor, então o
 * diálogo manda só o que mudou.
 */

"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Button, Dialog, Field, Input, Select, Textarea } from "@/components/ui";
import { RequestFailed, api } from "@/lib/api-client";
import type { Task, Priority } from "@/lib/types";

const PRIORIDADE: Record<Priority, string> = {
  low: "Baixa",
  normal: "Normal",
  high: "Alta",
  urgent: "Urgente",
};

export function EditarTarefaDialog({
  task,
  onClose,
}: {
  task: Task;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();

  const [titulo, setTitulo] = useState(task.title);
  const [notas, setNotas] = useState(task.notes ?? "");
  const [prazo, setPrazo] = useState(task.due_date ?? "");
  // `null` no banco significa "normal" na tela: o seletor precisa de
  // um valor, e inventar uma opção vazia só adiciona um estado a mais.
  const [prioridade, setPrioridade] = useState<Priority>(task.priority ?? "normal");
  const [estimado, setEstimado] = useState(
    task.estimated_min != null ? String(task.estimated_min) : "",
  );
  const [erro, setErro] = useState<string | null>(null);

  const minutos = estimado.trim() === "" ? null : Number(estimado);
  const estimativaValida =
    minutos === null || (Number.isFinite(minutos) && minutos >= 1 && minutos <= 1440);
  const podeSalvar = titulo.trim().length > 0 && estimativaValida;

  const salvar = useMutation({
    mutationFn: () =>
      api.patch<Task>(`/tasks/${task.id}`, {
        title: titulo.trim(),
        notes: notas.trim() || null,
        // String vazia significa "sem prazo", e `null` é como a rota
        // representa isso — mandar "" viraria erro de validação.
        due_date: prazo || null,
        priority: prioridade,
        estimated_min: minutos,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["tasks"] });
      onClose();
    },
    onError: (err) =>
      setErro(
        err instanceof RequestFailed ? err.message : "Não consegui salvar esta tarefa.",
      ),
  });

  return (
    <Dialog
      open
      onClose={onClose}
      title="Editar tarefa"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button
            variant="primary"
            loading={salvar.isPending}
            disabled={!podeSalvar}
            onClick={() => salvar.mutate()}
          >
            Salvar
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="O quê" htmlFor="t-titulo" error={erro}>
          <Input
            id="t-titulo"
            value={titulo}
            autoFocus
            onChange={(e) => {
              setTitulo(e.target.value);
              setErro(null);
            }}
          />
        </Field>

        <div className="grid grid-cols-2 gap-2.5">
          <Field label="Prazo" htmlFor="t-prazo" hint="Opcional.">
            <Input
              id="t-prazo"
              type="date"
              value={prazo}
              onChange={(e) => setPrazo(e.target.value)}
            />
          </Field>
          <Field label="Prioridade" htmlFor="t-prio">
            <Select
              id="t-prio"
              value={prioridade}
              onChange={(e) => setPrioridade(e.target.value as Priority)}
            >
              {(Object.keys(PRIORIDADE) as Priority[]).map((p) => (
                <option key={p} value={p}>
                  {PRIORIDADE[p]}
                </option>
              ))}
            </Select>
          </Field>
        </div>

        <Field
          label="Quanto tempo leva"
          htmlFor="t-est"
          hint="Em minutos. É o palpite inicial ao reservar tempo."
          error={estimativaValida ? undefined : "Use entre 1 e 1440 minutos."}
        >
          <Input
            id="t-est"
            type="number"
            min={1}
            max={1440}
            value={estimado}
            onChange={(e) => setEstimado(e.target.value)}
          />
        </Field>

        <Field label="Observações" htmlFor="t-notas" hint="Opcional.">
          <Textarea
            id="t-notas"
            rows={3}
            value={notas}
            onChange={(e) => setNotas(e.target.value)}
          />
        </Field>
      </div>
    </Dialog>
  );
}
