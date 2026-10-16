/**
 * A primeira tela de uma conversa (§8.1).
 *
 * Os quatro exemplos eram todos de execução: "marca isso", "o que eu
 * tenho". Ensinavam que o Age serve para dar ordem, e a capacidade que
 * ninguém descobria sozinho é a outra, a de perguntar antes de
 * decidir e deixar a agenda para depois.
 *
 * O teste guarda a proporção. Não é sobre a redação exata de cada
 * exemplo, é sobre a tela mostrar os dois modos de uso.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChatEmptyState } from "@/components/chat/messages";

function montar() {
  const onPick = vi.fn();
  render(<ChatEmptyState calendarName="Calendário do Age" onPick={onPick} />);
  return onPick;
}

describe("exemplos da tela vazia", () => {
  it("mostra que dá para pedir conselho, não só mandar marcar", () => {
    /*
     * O modo que ninguém descobre sozinho: chegar sem saber o que
     * quer. Sem um exemplo assim, a tela ensina que o Age só executa
     * ordem.
     *
     * A asserção é sobre a DICA, não sobre a frase ter "?". Minha
     * primeira versão procurava interrogação e passava mesmo com os
     * quatro exemplos de execução, porque "o que eu tenho pra fazer
     * essa semana?" é pergunta na forma e consulta na intenção.
     */
    montar();
    const dicas = screen.getAllByRole("button").map((b) => b.textContent ?? "");

    const temConselho = dicas.some(
      (t) => t.includes("agenda depois") || t.includes("ajuda a priorizar"),
    );

    expect(temConselho).toBe(true);
  });

  it("mantém exemplos de execução direta", () => {
    // O contrapeso: quem já sabe o que quer não deve achar que precisa
    // conversar antes.
    montar();
    expect(screen.getByText(/prova dia 20/)).toBeTruthy();
  });

  it("promete aprovação antes de qualquer escrita", () => {
    // §5.2 é a garantia central do produto, e é o que faz alguém se
    // sentir seguro mandando a primeira mensagem.
    montar();
    expect(screen.getByText(/sem você aprovar antes/)).toBeTruthy();
  });

  it("diz em qual agenda vai escrever", () => {
    montar();
    expect(screen.getByText("Calendário do Age")).toBeTruthy();
  });

  it("clicar num exemplo envia o texto dele", () => {
    const onPick = montar();
    const botoes = screen.getAllByRole("button");
    botoes[0].click();

    expect(onPick).toHaveBeenCalledTimes(1);
    expect(typeof onPick.mock.calls[0][0]).toBe("string");
    expect(onPick.mock.calls[0][0].length).toBeGreaterThan(10);
  });

  it("nenhum exemplo usa travessão", () => {
    // A proibição valia só para o modelo; a interface nunca esteve sob
    // essa regra, e a incoerência aparecia lado a lado na mesma tela.
    montar();
    const texto = screen
      .getAllByRole("button")
      .map((b) => b.textContent ?? "")
      .join(" ");

    expect(texto).not.toContain("—");
    expect(texto).not.toContain("–");
  });
});
