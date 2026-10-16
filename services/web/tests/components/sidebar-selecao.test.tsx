/**
 * Seleção múltipla e apagar em lote na sidebar (§6.2.4).
 *
 * Apagar uma por uma pelo menu de três pontos já existia: abrir o menu,
 * achar "Apagar", confirmar. Com 20 conversas acumuladas isso são
 * dezenas de cliques, e o pedido foi "ter a opção de apagar todos, ou
 * um local onde eu possa selecionar os que quero apagar".
 *
 * Os dois riscos que estes testes guardam:
 *
 *  1. apagar sem confirmar — é a ação mais destrutiva desta tela;
 *  2. clicar para marcar e acabar NAVEGANDO para a conversa, que
 *     tiraria a pessoa da lista no meio da escolha.
 */

import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ConversationSidebar } from "@/components/conversation-list/sidebar";
import type { Conversation } from "@/lib/types";

const TZ = "America/Sao_Paulo";

function conversa(id: string, title: string): Conversation {
  return {
    id,
    title,
    title_is_auto: false,
    provider_key: null,
    model: null,
    target_default: "event",
    pinned_at: null,
    archived_at: null,
    last_message_at: "2026-10-16T12:00:00Z",
    message_count: 2,
    created_at: "2026-10-16T12:00:00Z",
    applied_events: 0,
    applied_tasks: 0,
  };
}

const TRES = [
  conversa("a", "Prova de matemática"),
  conversa("b", "Desmarca tudo"),
  conversa("c", "Dentista quinta"),
];

function montar(over: { onDeleteMany?: (ids: string[]) => void } = {}) {
  const props = {
    conversations: TRES,
    counts: {},
    activeId: null,
    timezone: TZ,
    onNew: vi.fn(),
    onSearch: vi.fn(),
    onRename: vi.fn(),
    onPin: vi.fn(),
    onArchive: vi.fn(),
    onDelete: vi.fn(),
    onFork: vi.fn(),
    ...over,
  };
  render(<ConversationSidebar {...props} />);
  return props;
}

/** `fireEvent` em vez de `user-event`: o projeto não tem esse pacote,
 *  e um clique simples não justifica uma dependência nova. */
async function clicar(alvo: HTMLElement) {
  await act(async () => {
    fireEvent.click(alvo);
  });
}

async function entrarNaSelecao() {
  await clicar(screen.getByText("Selecionar conversas"));
}

describe("modo de seleção", () => {
  it("começa escondido, para não poluir a navegação", () => {
    // A lista é para navegar. Caixa de seleção em cada linha cobra
    // atenção de quem só quer trocar de conversa.
    montar({ onDeleteMany: vi.fn() });
    expect(screen.queryByText("Selecionar todas")).toBeNull();
    expect(screen.getByText("Selecionar conversas")).toBeTruthy();
  });

  it("marcar uma conversa não navega para ela", async () => {
    // O risco 2. No modo seleção a linha vira rótulo da caixa, não
    // link: um clique que fizesse as duas coisas tiraria a pessoa da
    // lista no meio da escolha.
    montar({ onDeleteMany: vi.fn() });
    await entrarNaSelecao();

    expect(screen.queryByRole("link", { name: /Prova de matemática/ })).toBeNull();

    const caixa = screen.getByLabelText(/Prova de matemática/);
    await clicar(caixa);
    expect((caixa as HTMLInputElement).checked).toBe(true);
  });

  it("'selecionar todas' marca tudo e o contador acompanha", async () => {
    montar({ onDeleteMany: vi.fn() });
    await entrarNaSelecao();

    expect(screen.getByText("0 de 3")).toBeTruthy();
    await clicar(screen.getByLabelText("Selecionar todas"));
    expect(screen.getByText("3 de 3")).toBeTruthy();
  });

  it("clicar de novo em 'selecionar todas' desmarca tudo", async () => {
    // Sem isto, quem marca tudo por engano não tem volta a não ser
    // desmarcar uma por uma.
    montar({ onDeleteMany: vi.fn() });
    await entrarNaSelecao();

    const todas = screen.getByLabelText("Selecionar todas");
    await clicar(todas);
    await clicar(todas);
    expect(screen.getByText("0 de 3")).toBeTruthy();
  });

  it("o botão de apagar fica inerte enquanto nada está marcado", async () => {
    montar({ onDeleteMany: vi.fn() });
    await entrarNaSelecao();

    const botao = screen.getByRole("button", { name: /^Apagar/ });
    expect((botao as HTMLButtonElement).disabled).toBe(true);
  });

  it("cancelar sai do modo e esquece a seleção", async () => {
    // Voltar ao modo seleção com as caixas ainda marcadas seria uma
    // armadilha: o próximo "Apagar" pegaria escolhas antigas.
    montar({ onDeleteMany: vi.fn() });
    await entrarNaSelecao();

    await clicar(screen.getByLabelText("Selecionar todas"));
    await clicar(screen.getByRole("button", { name: "Cancelar" }));

    await entrarNaSelecao();
    expect(screen.getByText("0 de 3")).toBeTruthy();
  });
});

describe("confirmação antes de apagar", () => {
  it("não apaga nada sem passar pelo diálogo", async () => {
    // O risco 1. Marcar e clicar em "Apagar" abre a confirmação; não
    // chama a API.
    const onDeleteMany = vi.fn();
    montar({ onDeleteMany });
    await entrarNaSelecao();

    await clicar(screen.getByLabelText(/Prova de matemática/));
    await clicar(screen.getByRole("button", { name: /^Apagar 1/ }));

    expect(onDeleteMany).not.toHaveBeenCalled();
    expect(screen.getByText("Apagar 1 conversa?")).toBeTruthy();
  });

  it("apaga só depois de confirmar, e manda os ids marcados", async () => {
    const onDeleteMany = vi.fn();
    montar({ onDeleteMany });
    await entrarNaSelecao();

    await clicar(screen.getByLabelText(/Prova de matemática/));
    await clicar(screen.getByLabelText(/Dentista quinta/));
    await clicar(screen.getByRole("button", { name: /^Apagar 2/ }));

    const dialogo = screen.getByRole("dialog");
    await clicar(within(dialogo).getByRole("button", { name: "Apagar" }));

    expect(onDeleteMany).toHaveBeenCalledTimes(1);
    expect(onDeleteMany.mock.calls[0][0].sort()).toEqual(["a", "c"]);
  });

  it("diz 'todas' quando é tudo, porque a consequência é outra", async () => {
    // "Apagar 3 conversas?" e "Apagar todas as conversas?" pedem
    // atenções diferentes de quem lê.
    montar({ onDeleteMany: vi.fn() });
    await entrarNaSelecao();

    await clicar(screen.getByLabelText("Selecionar todas"));
    await clicar(screen.getByRole("button", { name: /^Apagar 3/ }));

    expect(screen.getByText("Apagar todas as conversas?")).toBeTruthy();
  });

  it("avisa que a agenda não é afetada (§6.2.3)", async () => {
    // Apagar conversa NÃO desfaz o que foi aprovado. Quem não sabe
    // disso hesita em limpar o histórico, ou limpa achando que está
    // desmarcando compromissos.
    montar({ onDeleteMany: vi.fn() });
    await entrarNaSelecao();

    await clicar(screen.getByLabelText("Selecionar todas"));
    await clicar(screen.getByRole("button", { name: /^Apagar 3/ }));

    expect(screen.getByText(/continua na agenda/)).toBeTruthy();
    expect(screen.getByText(/30 dias/)).toBeTruthy();
  });

  it("cancelar no diálogo não apaga", async () => {
    const onDeleteMany = vi.fn();
    montar({ onDeleteMany });
    await entrarNaSelecao();

    await clicar(screen.getByLabelText("Selecionar todas"));
    await clicar(screen.getByRole("button", { name: /^Apagar 3/ }));

    const dialogo = screen.getByRole("dialog");
    await clicar(within(dialogo).getByRole("button", { name: "Cancelar" }));

    expect(onDeleteMany).not.toHaveBeenCalled();
  });
});

describe("quando apagar em lote não está disponível", () => {
  it("sem onDeleteMany, o modo seleção nem aparece", () => {
    // A sidebar é usada em mais de um lugar; quem não passa o callback
    // não deve ganhar um botão que não faz nada.
    montar();
    expect(screen.queryByText("Selecionar conversas")).toBeNull();
  });
});
