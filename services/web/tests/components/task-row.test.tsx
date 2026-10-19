/**
 * `<TaskRow>` — o aviso que §8.1 chama de "o sinal que o produto existe
 * para dar".
 *
 * Este é o primeiro teste de componente do projeto, e a escolha do
 * componente não é arbitrária. §8.1:
 *
 *   "Cada tarefa mostra se tem tempo reservado (§5.7.1) — é a
 *   informação que diferencia o Age de um to-do comum."
 *
 * A decisão de mostrar o aviso vive na UI (`needsTime`, em
 * `task-list.tsx`), combinando `is_overdue` do backend com um cálculo
 * de prazo próximo feito aqui. Ou seja: é regra de domínio num
 * componente, que é exatamente onde um bug fica invisível — o cartão
 * renderiza, nada quebra, e o aviso simplesmente não aparece.
 *
 * Três bugs dessa natureza já foram encontrados neste projeto olhando a
 * tela, um por um: `PlanItem.op` com caixa divergente (o diff de
 * movimentação nunca renderizou), o estado vazio do chat que ignorava
 * `streaming`, e o cabeçalho desalinhado. Nenhum teste existente os
 * pegaria, porque nenhum renderizava componente.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TaskRow } from "@/components/tasks/task-list";
import type { Reserved, Task } from "@/lib/types";

const TZ = "America/Sao_Paulo";

/** Dias a partir de hoje, em ISO — nunca data literal.
 *
 * `smoke-ui.sh` registra por que: a primeira versão dele tinha
 * "2026-11-20" escrito dentro, funcionou algumas semanas e depois passou
 * a falhar reportando um bug que não existia. Um teste de prazo com data
 * fixa tem validade.
 */
function emDias(dias: number): string {
  const d = new Date();
  d.setDate(d.getDate() + dias);
  return d.toISOString();
}

function tarefa(over: Partial<Task> = {}): Task {
  return {
    id: "t1",
    ref_kind: "local",
    list_id: "l1",
    title: "Revisar capítulo 2",
    notes: null,
    due_date: null,
    due_at: null,
    status: "open",
    completed_at: null,
    priority: null,
    labels: [],
    estimated_min: null,
    position: null,
    is_overdue: false,
    reserved: [],
    reserved_minutes: 0,
    provider_name: null,
    dropped_fields: [],
    ...over,
  };
}

function bloco(over: Partial<Reserved> = {}): Reserved {
  return {
    event_ref_kind: "local",
    event_ref_id: "e1",
    title: "Revisar capítulo 2",
    start: emDias(2),
    end: emDias(2),
    minutes: 180,
    ...over,
  };
}

function montar(t: Task, omitir?: { tema?: string; bloco?: string }) {
  return render(
    <TaskRow
      task={t}
      timezone={TZ}
      omitir={omitir}
      onToggle={vi.fn()}
      onSchedule={vi.fn()}
      onDelete={vi.fn()}
    />,
  );
}

describe("TaskRow — o aviso de tempo reservado (§8.1)", () => {
  it("avisa quando o prazo está próximo e não há tempo reservado", () => {
    montar(tarefa({ due_at: emDias(3) }));

    expect(screen.getByText("sem tempo reservado")).toBeTruthy();
    // O botão é a parte acionável do aviso: §8.1 pede que o sinal venha
    // com a saída, não só com o diagnóstico.
    expect(screen.getByRole("button", { name: /reservar tempo/i })).toBeTruthy();
  });

  it("não avisa quando o prazo está longe", () => {
    // 30 dias: fora do horizonte de 7 dias que `/tasks/overview` usa.
    const { container } = montar(tarefa({ due_at: emDias(30) }));

    // A linha "sem tempo reservado" continua ali — é um fato sobre a
    // tarefa. O que NÃO deve existir é o destaque de urgência.
    expect(screen.getByText("sem tempo reservado")).toBeTruthy();
    expect(container.querySelector(".text-warn")).toBeNull();
    expect(container.querySelector(".border-warn\\/35")).toBeNull();
  });

  it("avisa, em vermelho, quando a tarefa está atrasada", () => {
    // `is_overdue` vem do backend porque depende do fuso e da regra
    // "prazo só com data vale até o fim do dia" (§9.5).
    const { container } = montar(tarefa({ due_at: emDias(-2), is_overdue: true }));

    expect(screen.getByText("sem tempo reservado")).toBeTruthy();
    // A faixa lateral distingue atrasado (danger) de próximo (warn).
    expect(container.querySelector(".bg-danger")).toBeTruthy();
  });

  it("mostra o tempo reservado em vez do aviso quando existe", () => {
    montar(tarefa({ due_at: emDias(3), reserved: [bloco()], reserved_minutes: 180 }));

    expect(screen.queryByText("sem tempo reservado")).toBeNull();
    expect(screen.queryByRole("button", { name: /reservar tempo/i })).toBeNull();
    // "3h reservadas", com a concordância certa no plural.
    expect(screen.getByText(/3h reservadas/)).toBeTruthy();
  });

  it("concorda no singular com exatamente uma hora", () => {
    montar(tarefa({ reserved: [bloco({ minutes: 60 })], reserved_minutes: 60 }));
    expect(screen.getByText(/1h reservada(?!s)/)).toBeTruthy();
  });

  it("não avisa nem oferece reservar numa tarefa concluída", () => {
    // Prazo vencido E sem tempo reservado, mas feita: cobrar tempo para
    // algo que já terminou é ruído, e o cartão precisa ficar quieto.
    const { container } = montar(
      tarefa({ status: "done", due_at: emDias(-5), is_overdue: true }),
    );

    expect(screen.queryByText("sem tempo reservado")).toBeNull();
    expect(screen.queryByRole("button", { name: /reservar tempo/i })).toBeNull();
    expect(container.querySelector(".border-warn\\/35")).toBeNull();
  });

  it("não avisa quando a tarefa não tem prazo nenhum", () => {
    // Sem prazo não há urgência a comunicar: o aviso de §8.1 é sobre o
    // cruzamento de prazo com agenda, e aqui falta metade.
    const { container } = montar(tarefa({ due_at: null, due_date: null }));

    expect(screen.getByText("sem prazo")).toBeTruthy();
    expect(container.querySelector(".border-warn\\/35")).toBeNull();
  });

  it("trata prazo só com data como válido até o fim do dia (§9.5)", () => {
    // `due_date` sem hora é o caso comum de provedor pobre (§3.3.3), e
    // a lista precisa tratá-lo igual: hoje, só com data, ainda está no
    // horizonte e pede aviso.
    const hoje = new Date().toISOString().slice(0, 10);
    const { container } = montar(tarefa({ due_date: hoje }));

    expect(container.querySelector(".border-warn\\/35")).toBeTruthy();
  });

  it("mostra o que o provedor não guarda, no item (§3.3.2)", () => {
    // "o usuário precisa saber ao olhar a tarefa" — não só na tela de
    // conexão.
    montar(
      tarefa({
        provider_name: "Google Tasks",
        dropped_fields: ["prioridade", "duração"],
      }),
    );

    expect(
      screen.getByText(/Google Tasks não guarda prioridade, duração/),
    ).toBeTruthy();
  });

  it("a caixa de marcação é acessível por nome", () => {
    // §8.2: a caixa é desenhada, mas continua um `<input type=checkbox>`
    // por baixo — então teclado e leitor de tela seguem funcionando.
    montar(tarefa({ title: "Pagar IPVA" }));

    const caixa = screen.getByRole("checkbox", { name: /concluir pagar ipva/i });
    expect(caixa).toBeTruthy();
    expect((caixa as HTMLInputElement).checked).toBe(false);
  });
});

describe("TaskRow — não repete o que o cabeçalho do grupo já diz", () => {
  it("agrupado por tema, o item não repete a etiqueta do tema", () => {
    /*
     * Só apareceu vendo a tela: embaixo de um cabeçalho "FUNCOES EM
     * PYTHON", as cinco tarefas traziam a badge "Funcoes em Python".
     * As OUTRAS etiquetas continuam, porque essas o cabeçalho não diz.
     */
    montar(tarefa({ labels: ["Funções em Python", "urgente"] }), {
      tema: "Funções em Python",
    });

    expect(screen.queryByText("Funções em Python")).toBeNull();
    expect(screen.getByText("urgente")).toBeTruthy();
  });

  it("agrupado por horário, o item não repete o bloco do cabeçalho", () => {
    montar(tarefa({ reserved: [bloco({ event_ref_id: "e1" })] }), {
      bloco: "e1",
    });

    expect(screen.queryByText(/reservad/)).toBeNull();
  });

  it("mas NÃO diz 'sem tempo reservado' quando o bloco só foi omitido", () => {
    /*
     * A armadilha: filtrar o bloco zerava `hasReserved`, e o item caía
     * no ramo contrário, oferecendo "reservar tempo" para uma tarefa
     * que TEM horário. Dizer o oposto da verdade é pior que repetir.
     *
     * Sabotagem: trocar `task.reserved.length > 0` por `blocos.length`
     * na condição do ramo.
     */
    montar(tarefa({ reserved: [bloco({ event_ref_id: "e1" })] }), {
      bloco: "e1",
    });

    expect(screen.queryByText("sem tempo reservado")).toBeNull();
    expect(screen.queryByRole("button", { name: /reservar tempo/i })).toBeNull();
  });

  it("os outros blocos continuam à vista", () => {
    // "Esta tarefa também tem hora na quarta" é informação que o
    // cabeçalho de terça não dá.
    montar(
      tarefa({
        reserved: [
          bloco({ event_ref_id: "e1" }),
          bloco({ event_ref_id: "e2", minutes: 60 }),
        ],
      }),
      { bloco: "e1" },
    );

    expect(screen.getAllByText(/reservad/)).toHaveLength(1);
  });

  it("sem agrupamento especial, nada é escondido", () => {
    // Contrapeso: as visões antigas continuam mostrando tudo.
    montar(tarefa({ labels: ["TCC"], reserved: [bloco()] }));

    expect(screen.getByText("TCC")).toBeTruthy();
    expect(screen.getAllByText(/reservad/).length).toBeGreaterThan(0);
  });
});
