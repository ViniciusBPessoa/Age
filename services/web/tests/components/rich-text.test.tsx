/**
 * `<RichText>` — o Markdown que o modelo escreve (§8.1).
 *
 * Este arquivo existe por um defeito que chegou ao usuário: a resposta
 * do Age aparecia com ```` ``` ````, `**` e `|---|` literais na tela.
 * O relato foi direto: "o texto está completamente mal formatado,
 * absurdamente mal formatado, completamente incompreensível".
 *
 * A causa era `{text}` cru num `<p>` com `whitespace-pre-wrap`. O
 * modelo escreve em Markdown — todo LLM escreve — e a tela não
 * interpretava nada.
 *
 * Os casos aqui vêm de respostas REAIS medidas contra a API do
 * DeepSeek durante os testes, não de Markdown inventado. É o que
 * garante que o renderizador cobre o que este modelo de fato produz.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RichText } from "@/components/chat/rich-text";

function montar(texto: string) {
  return render(<RichText text={texto} />);
}

describe("RichText — o Markdown do modelo", () => {
  it("não deixa cerca de código na tela", () => {
    // O sintoma exato relatado: "tem algumas partes que tem três
    // aspas". Eram as cercas ``` do bloco de código.
    const { container } = montar("Plano:\n```\nsessão 1\n```");

    expect(container.textContent).not.toContain("```");
    expect(container.querySelector("pre")).toBeTruthy();
    expect(screen.getByText("sessão 1")).toBeTruthy();
  });

  it("preserva o conteúdo do bloco sem interpretar o que tem dentro", () => {
    // `**` dentro de código é `**`, literal. Interpretar quebraria
    // qualquer trecho que o modelo mostrasse como exemplo.
    const { container } = montar("```\ntotal **8,3h** em 7 dias\n```");

    expect(container.querySelector("pre")?.textContent).toContain("**8,3h**");
    expect(container.querySelector("strong")).toBeNull();
  });

  it("renderiza bloco de código ainda não fechado, durante o streaming", () => {
    // Enquanto o modelo escreve, a cerca final não chegou. Exigi-la
    // faria o bloco inteiro desaparecer da tela no meio da resposta —
    // e o texto reaparecer de repente no `done`.
    const { container } = montar("Plano:\n```\nlinha parcial");

    expect(container.querySelector("pre")?.textContent).toContain("linha parcial");
  });

  it("transforma negrito em <strong>, sem asteriscos visíveis", () => {
    const { container } = montar("O prazo é **30 de outubro**.");

    expect(screen.getByText("30 de outubro").tagName).toBe("STRONG");
    expect(container.textContent).not.toContain("**");
  });

  it("transforma código inline em <code>", () => {
    const { container } = montar("Rode `docker compose up` antes.");

    expect(screen.getByText("docker compose up").tagName).toBe("CODE");
    expect(container.textContent).not.toContain("`");
  });

  it("negrito vence itálico na mesma linha", () => {
    // Com `*` tratado antes de `**`, "**x**" viraria itálico vazio
    // seguido de lixo. A ordem na regex é o que impede isso.
    const { container } = montar("**importante** e *detalhe*");

    expect(container.querySelector("strong")?.textContent).toBe("importante");
    expect(container.querySelector("em")?.textContent).toBe("detalhe");
  });

  it("renderiza lista com marcador próprio, sem o hífen do texto", () => {
    const { container } = montar(
      "Tópicos:\n- Autovalores\n- Espaços vetoriais\n- Transformações",
    );

    const itens = container.querySelectorAll("li");
    expect(itens.length).toBe(3);
    expect(itens[0].textContent).toBe("Autovalores");
    // O hífen do Markdown não deve sobrar como caractere.
    expect(itens[0].textContent?.startsWith("-")).toBe(false);
  });

  it("renderiza lista numerada", () => {
    const { container } = montar("1. Primeiro\n2. Segundo");

    const itens = container.querySelectorAll("li");
    expect(itens.length).toBe(2);
    expect(itens[1].textContent).toBe("Segundo");
  });

  it("renderiza tabela de verdade, sem pipes na tela", () => {
    // Caso real: o modelo respondeu o plano de estudo em tabela.
    const { container } = montar(
      "| Dia | Tópico |\n|---|---|\n| 17/10 | Autovalores |\n| 18/10 | Bases |",
    );

    expect(container.querySelector("table")).toBeTruthy();
    expect(container.querySelectorAll("th").length).toBe(2);
    expect(container.querySelectorAll("tbody tr").length).toBe(2);
    expect(screen.getByText("Autovalores")).toBeTruthy();
    expect(container.textContent).not.toContain("|---|");
  });

  it("a tabela rola sozinha, sem empurrar a página", () => {
    // Uma tabela de plano passa da largura no celular. Sem o
    // `overflow-x-auto` no entorno, ela cria rolagem horizontal na
    // página inteira — defeito que o chat já teve antes.
    const { container } = montar("| a | b |\n|---|---|\n| 1 | 2 |");

    const volta = container.querySelector("table")?.parentElement;
    expect(volta?.className).toMatch(/overflow-x-auto/);
  });

  it("renderiza títulos sem os sustenidos", () => {
    const { container } = montar("## Plano de estudo\n\nTexto.");

    expect(container.textContent).not.toContain("#");
    expect(screen.getByText("Plano de estudo")).toBeTruthy();
  });

  it("junta linhas do mesmo parágrafo e separa parágrafos", () => {
    const { container } = montar("Linha um\nainda o mesmo.\n\nOutro.");

    expect(container.querySelectorAll("p").length).toBe(2);
  });

  it("não interpreta HTML vindo do modelo", () => {
    // Defesa em profundidade: nada aqui usa `dangerouslySetInnerHTML`,
    // então uma tag devolvida pelo modelo aparece como texto. Este
    // teste trava essa propriedade contra uma "melhoria" futura.
    const { container } = montar("<script>alert(1)</script> e <b>x</b>");

    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect(container.textContent).toContain("<script>");
  });

  it("aguenta texto vazio sem quebrar", () => {
    // O primeiro delta do streaming pode chegar vazio.
    const { container } = montar("");
    expect(container).toBeTruthy();
  });

  it("mantém o texto de uma resposta real legível", () => {
    // Resposta medida contra o DeepSeek nos testes de agenda.
    const real =
      "Sua agenda está **vazia** nos próximos 7 dias (15/10 a 22/10) — " +
      "nenhum compromisso registrado.\n\n" +
      "Se quiser, posso olhar sua lista de tarefas.";
    const { container } = montar(real);

    expect(container.textContent).not.toContain("**");
    expect(screen.getByText("vazia").tagName).toBe("STRONG");
    expect(container.querySelectorAll("p").length).toBe(2);
  });
});
