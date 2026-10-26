/**
 * Editar tarefa e evento, e criar evento à mão (§8.1).
 *
 * Cada teste traz a sabotagem que precisa derrubá-lo.
 *
 * Dois relatos:
 *
 * - *"não existe nenhum botão para fazer edição do meu to-do uma vez
 *   que ele foi criado. A mesma coisa serve para um evento na agenda."*
 * - *"ele não permite a criação manual de horários. Se eu quisesse vir
 *   no meu calendário, eu mesmo marcar alguma coisa, eu não consigo."*
 *
 * `PATCH /tasks/{id}` existia desde o M4 e nenhuma tela o chamava:
 * corrigir um título exigia apagar e refazer, perdendo o tempo já
 * reservado.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TaskRow } from "@/components/tasks/task-list";
import {
  campoData,
  campoHora,
  instante,
} from "@/components/calendar-timeline/evento-dialog";
import type { Task } from "@/lib/types";

afterEach(cleanup);

function tarefa(over: Partial<Task> = {}): Task {
  return {
    id: "t1",
    ref_kind: "local_tasks",
    list_id: "l1",
    title: "Revisar capítulo 2",
    notes: null,
    due_date: null,
    due_at: null,
    status: "open",
    completed_at: null,
    priority: "normal",
    labels: [],
    estimated_min: 60,
    position: 0,
    is_overdue: false,
    // Lista vazia, não `null`: o componente faz `.filter` direto.
    reserved: [],
    dropped_fields: [],
    provider_name: null,
    ...over,
  } as unknown as Task;
}

function montarLista(props: Record<string, unknown> = {}) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <ul>
        <TaskRow
          task={tarefa()}
          timezone="America/Sao_Paulo"
          onToggle={vi.fn()}
          onSchedule={vi.fn()}
          onDelete={vi.fn()}
          {...props}
        />
      </ul>
    </QueryClientProvider>,
  );
}

describe("o botão de editar tarefa", () => {
  it("existe e chama com a tarefa certa", () => {
    /*
     * A sabotagem central: não renderizar o botão — o estado anterior,
     * em que `PATCH /tasks/{id}` existia e nenhuma tela o chamava.
     */
    const onEdit = vi.fn();
    montarLista({ onEdit });

    const botao = screen.getByRole("button", {
      name: /editar revisar capítulo 2/i,
    });
    fireEvent.click(botao);

    expect(onEdit).toHaveBeenCalledTimes(1);
    expect(onEdit.mock.calls[0][0].id).toBe("t1");
  });

  it("tem nome acessível, não só o ícone", () => {
    /*
     * Sabotagem: remover o `aria-label`.
     *
     * Um `<button>` com só um `<svg>` dentro é botão sem nome para
     * leitor de tela — e o botão só aparece no hover, então quem
     * navega por teclado depende inteiramente do nome. §8.2.
     */
    montarLista({ onEdit: vi.fn() });
    expect(
      screen.getByRole("button", { name: /editar revisar capítulo 2/i }),
    ).not.toBeNull();
  });

  it("sem `onEdit`, nenhum botão de editar", () => {
    /*
     * Sabotagem: renderizar sempre.
     *
     * A prop é opcional porque nem toda lista oferece edição; um botão
     * que não faz nada é pior que botão nenhum.
     */
    montarLista();
    expect(screen.queryByRole("button", { name: /^editar/i })).toBeNull();
  });
});

describe("os campos de data e hora do evento", () => {
  it("formata no fuso LOCAL", () => {
    /*
     * Sabotagem: `toISOString().slice(0, 10)`.
     *
     * Em São Paulo, 22h do dia 25 vira dia 26 em UTC: o diálogo abriria
     * no dia seguinte ao que a pessoa clicou. Só aparece à noite, que é
     * justamente quando se marca compromisso para o dia seguinte.
     */
    const noite = new Date(2026, 9, 25, 22, 0);
    expect(campoData(noite)).toBe("2026-10-25");
    expect(campoHora(noite)).toBe("22:00");
  });

  it("lê de volta sem mover a hora", () => {
    /*
     * Sabotagem: montar com `Z` no fim.
     *
     * Trataria a hora digitada como UTC, e o dentista das 15h entraria
     * às 12h na agenda.
     */
    const d = instante("2026-10-26", "15:00")!;
    expect(d.getHours()).toBe(15);
    expect(d.getDate()).toBe(26);
  });

  it("campo vazio não vira data inválida", () => {
    // Sabotagem: devolver `new Date("")`, que viaja como `Invalid Date`
    // e chega ao servidor como `null` silencioso.
    expect(instante("", "15:00")).toBeNull();
    expect(instante("2026-10-26", "")).toBeNull();
  });

  it("zero à esquerda em tudo", () => {
    // Sabotagem: concatenar sem `padStart`. `<input type="time">`
    // recusa `9:5` e o campo abre vazio.
    const cedo = new Date(2026, 0, 3, 8, 7);
    expect(campoData(cedo)).toBe("2026-01-03");
    expect(campoHora(cedo)).toBe("08:07");
  });
});
