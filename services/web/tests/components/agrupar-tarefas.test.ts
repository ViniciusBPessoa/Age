/**
 * As duas visões que a lista de tarefas ganhou.
 *
 * Pedido: "um agrupamento que tem a ver com horário marcado, e outro
 * que tem a ver com tema. Eu, como usuário, escolho qual eu quero."
 *
 * Por horário: o cabeçalho é a sessão, e dentro o que fazer naquela
 * hora. Por tema: o cabeçalho é o assunto, e cada item mostra quando
 * acontece — "se eu passar do que já deveria ter aprendido, eu posso
 * começar outros itens".
 *
 * `groupTasks` é função pura de propósito: é o que permite testar as
 * cinco visões sem montar a tela inteira.
 */

import { describe, expect, it } from "vitest";

import { groupTasks } from "@/components/tasks/tasks-screen";
import type { Reserved, Task } from "@/lib/types";

const TZ = "America/Sao_Paulo";

function bloco(id: string, titulo: string, inicio: string): Reserved {
  return {
    event_ref_kind: "local_events",
    event_ref_id: id,
    title: titulo,
    start: inicio,
    end: inicio.replace("T16:", "T17:"),
    minutes: 60,
  };
}

function tarefa(over: Partial<Task> & { id: string }): Task {
  return {
    ref_kind: "local_tasks",
    list_id: "l1",
    title: "Tópico",
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
  } as Task;
}

const S1 = bloco("ev-1", "Estudo: Funções em Python", "2026-10-19T16:15:00-03:00");
const S2 = bloco("ev-2", "Estudo: Funções em Python", "2026-10-20T16:15:00-03:00");

describe("por horário", () => {
  it("o cabeçalho é a sessão e dentro vem o que fazer nela", () => {
    const grupos = groupTasks(
      [
        tarefa({ id: "t1", title: "Definir e chamar", reserved: [S1] }),
        tarefa({ id: "t2", title: "Escopo", reserved: [S1] }),
      ],
      "horario",
      [],
      TZ,
    );

    expect(grupos).toHaveLength(1);
    expect(grupos[0].label).toBe("Estudo: Funções em Python");
    expect(grupos[0].tasks.map((t) => t.id)).toEqual(["t1", "t2"]);
  });

  it("as sessões vêm em ordem cronológica", () => {
    /*
     * Sabotagem: ordenar por rótulo. As duas sessões se chamam IGUAL,
     * então só a data distingue — e a ordem vira a de inserção.
     */
    const grupos = groupTasks(
      [tarefa({ id: "t2", reserved: [S2] }), tarefa({ id: "t1", reserved: [S1] })],
      "horario",
      [],
      TZ,
    );

    expect(grupos.map((g) => g.id)).toEqual(["ev-1", "ev-2"]);
  });

  it("duas sessões de mesmo nome não colapsam numa só", () => {
    /*
     * O defeito que o `id` conserta: a chave do React era o rótulo, e
     * "Estudo: Funções em Python (1/2)" e "(2/2)" quando renomeados
     * para o mesmo título viravam uma seção só.
     */
    const grupos = groupTasks(
      [tarefa({ id: "t1", reserved: [S1] }), tarefa({ id: "t2", reserved: [S2] })],
      "horario",
      [],
      TZ,
    );

    expect(grupos).toHaveLength(2);
    expect(new Set(grupos.map((g) => g.id)).size).toBe(2);
  });

  it("o cabeçalho diz o dia e a hora", () => {
    const [grupo] = groupTasks(
      [tarefa({ id: "t1", reserved: [S1] })],
      "horario",
      [],
      TZ,
    );

    expect(grupo.meta).toBeTruthy();
    expect(grupo.meta).toContain("16:15");
  });

  it("tarefa em duas sessões aparece nas duas", () => {
    /*
     * De propósito: a pergunta desta visão é "o que eu faço nesta
     * hora?". Esconder a tarefa na segunda sessão faria aquela hora
     * parecer mais vazia do que é.
     */
    const grupos = groupTasks(
      [tarefa({ id: "exerc", title: "Exercícios", reserved: [S1, S2] })],
      "horario",
      [],
      TZ,
    );

    expect(grupos).toHaveLength(2);
    expect(grupos.every((g) => g.tasks.some((t) => t.id === "exerc"))).toBe(true);
  });

  it("quem não tem horário fica num balde, por último", () => {
    /*
     * "*args e **kwargs ficou fora das 2h combinadas." Sumir com ela
     * seria perder a tarefa de vista — o oposto do que a lista serve.
     * Sabotagem: não criar o balde. Esta tarefa desaparece da tela.
     */
    const grupos = groupTasks(
      [
        tarefa({ id: "t1", reserved: [S1] }),
        tarefa({ id: "solta", title: "*args e **kwargs" }),
      ],
      "horario",
      [],
      TZ,
    );

    expect(grupos.at(-1)?.label).toBe("Sem horário");
    expect(grupos.at(-1)?.tasks.map((t) => t.id)).toEqual(["solta"]);
  });
});

describe("por tema", () => {
  it("agrupa pela primeira etiqueta", () => {
    const grupos = groupTasks(
      [
        tarefa({ id: "t1", labels: ["Funções em Python"] }),
        tarefa({ id: "t2", labels: ["Funções em Python"] }),
      ],
      "tema",
      [],
      TZ,
    );

    expect(grupos).toHaveLength(1);
    expect(grupos[0].label).toBe("Funções em Python");
  });

  it("etiqueta comum não quebra o tema em dois", () => {
    /*
     * Sabotagem: agrupar por `labels.join(",")`. A tarefa com
     * ["Funções em Python", "urgente"] sairia num grupo só dela.
     */
    const grupos = groupTasks(
      [
        tarefa({ id: "t1", labels: ["Funções em Python"] }),
        tarefa({ id: "t2", labels: ["Funções em Python", "urgente"] }),
      ],
      "tema",
      [],
      TZ,
    );

    expect(grupos).toHaveLength(1);
    expect(grupos[0].tasks).toHaveLength(2);
  });

  it("serve para qualquer área da vida, não só estudo", () => {
    const grupos = groupTasks(
      [
        tarefa({ id: "t1", labels: ["Academia"] }),
        tarefa({ id: "t2", labels: ["TCC"] }),
      ],
      "tema",
      [],
      TZ,
    );

    expect(grupos.map((g) => g.label)).toEqual(["Academia", "TCC"]);
  });

  it("quem não tem tema fica num balde, por último", () => {
    const grupos = groupTasks(
      [tarefa({ id: "t1", labels: ["TCC"] }), tarefa({ id: "t2" })],
      "tema",
      [],
      TZ,
    );

    expect(grupos.at(-1)?.label).toBe("Sem tema");
  });
});

describe("as visões que já existiam", () => {
  it("por prazo continua separando atrasada de concluída", () => {
    // Contrapeso: as visões novas não podem ter mexido nas antigas.
    const grupos = groupTasks(
      [
        tarefa({
          id: "t1",
          is_overdue: true,
          due_at: "2026-10-01T12:00:00-03:00",
        }),
        tarefa({ id: "t2", status: "done" }),
      ],
      "due",
      [],
      TZ,
    );

    expect(grupos.map((g) => g.label)).toEqual(["Atrasadas", "Concluídas"]);
  });

  it("todo grupo tem id, em todas as visões", () => {
    // Sem `id` o React cai de volta no rótulo e as seções colidem.
    for (const modo of ["due", "horario", "tema", "list", "priority"] as const) {
      const grupos = groupTasks(
        [tarefa({ id: "t1", labels: ["X"], reserved: [S1], priority: "high" })],
        modo,
        [
          {
            id: "l1",
            name: "Minhas tarefas",
            is_default: true,
            writable: true,
            color: null,
          },
        ],
        TZ,
      );

      expect(grupos.length).toBeGreaterThan(0);
      for (const g of grupos) expect(g.id).toBeTruthy();
    }
  });
});

describe("o item não repete o que o cabeçalho já diz", () => {
  it("por tema, a ordem dentro do grupo é a do relógio", () => {
    /*
     * "Se eu passar do que eu já deveria ter aprendido, eu posso
     * começar outros itens" — só dá para ler isso de cima para baixo
     * se o que acontece antes vier antes.
     *
     * Sabotagem: devolver `items` sem ordenar.
     */
    const grupos = groupTasks(
      [
        tarefa({ id: "depois", labels: ["X"], reserved: [S2] }),
        tarefa({ id: "antes", labels: ["X"], reserved: [S1] }),
      ],
      "tema",
      [],
      TZ,
    );

    expect(grupos[0].tasks.map((t) => t.id)).toEqual(["antes", "depois"]);
  });

  it("por tema, quem ainda não tem sessão vai para o fim", () => {
    // Não dá para dizer que atrasou algo que nunca foi marcado.
    const grupos = groupTasks(
      [
        tarefa({ id: "sem-hora", labels: ["X"] }),
        tarefa({ id: "com-hora", labels: ["X"], reserved: [S1] }),
      ],
      "tema",
      [],
      TZ,
    );

    expect(grupos[0].tasks.map((t) => t.id)).toEqual(["com-hora", "sem-hora"]);
  });
});
