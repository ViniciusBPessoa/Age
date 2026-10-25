/**
 * As fontes da busca na web, dentro da caixa de consultas (§5.1.3).
 *
 * Cada teste traz a sabotagem que precisa derrubá-lo.
 *
 * O relato: *"o Deep Search, eu não tenho nenhuma referência visual se
 * está funcionando ou não. Depois que ele procura, ele não escreve no
 * chat separado quais foram as referências utilizadas."*
 *
 * A caixa recolhível já existia — era o "7 consultas" do print. O que
 * faltava era conteúdo: no sucesso o worker mandava `summary = null`,
 * e as fontes morriam no backend.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { MessageList, type ToolTrace } from "@/components/chat/messages";

afterEach(cleanup);

const FONTES = [
  {
    titulo: "Edital do Enem 2026 é publicado",
    url: "https://guiadoestudante.abril.com.br/enem/edital/",
    site: "Guia do Estudante",
    confiavel: true,
  },
  {
    titulo: "Revision f6538409",
    url: "https://stackoverflow.com/revisions/f6538409",
    site: "Stack Overflow",
    confiavel: false,
  },
];

function montar(traces: ToolTrace[]) {
  return render(
    <MessageList
      messages={[]}
      traces={traces}
      streaming
      streamingText=""
      timezone="America/Sao_Paulo"
    />,
  );
}

const BUSCA: ToolTrace = {
  name: "web_search",
  label: "pesquisando na internet",
  done: true,
  ok: true,
  summary: "Guia do Estudante, Stack Overflow",
  fontes: FONTES,
};

describe("as fontes aparecem e levam ao site", () => {
  it("vira link para a página real", () => {
    /*
     * A sabotagem central: voltar `summary = null` no sucesso e não
     * mandar fontes — o estado que o usuário fotografou, com a caixa
     * dizendo só "7 consultas" e nada dentro.
     */
    montar([BUSCA]);

    const link = screen.getByRole("link", {
      name: /Edital do Enem 2026/i,
    });
    expect(link.getAttribute("href")).toBe(
      "https://guiadoestudante.abril.com.br/enem/edital/",
    );
  });

  it("abre em nova aba sem vazar a conversa", () => {
    /*
     * Sabotagem: tirar o `rel`.
     *
     * São sites de terceiros escolhidos por um modelo a partir de
     * resultados de busca — não dá para confiar no destino.
     */
    montar([BUSCA]);
    const link = screen.getByRole("link", { name: /Edital do Enem/i });

    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
    expect(link.getAttribute("rel")).toContain("noreferrer");
  });

  it("NÃO cria link para javascript: nem data:", () => {
    /*
     * A sabotagem que só este teste pega: aceitar qualquer esquema.
     *
     * A busca devolve resultado irrelevante com HTTP 200 (medido), então
     * conteúdo hostil chega por um caminho normal. Um `javascript:`
     * virando âncora seria XSS com um clique.
     */
    montar([
      {
        ...BUSCA,
        fontes: [
          { titulo: "clique aqui", url: "javascript:alert(1)" },
          { titulo: "ou aqui", url: "data:text/html,<script>x</script>" },
        ],
      },
    ]);

    expect(screen.queryByRole("link")).toBeNull();
    // Continua visível como texto, em vez de sumir calado.
    expect(screen.queryByText(/clique aqui/i)).not.toBeNull();
  });

  it("fonte fraca é marcada, não escondida", () => {
    /*
     * Sabotagem: filtrar `confiavel === false`, ou renderizar igual à
     * boa.
     *
     * Foi a escolha do usuário quando perguntei: mostrar avisando. As
     * duas sabotagens quebram a escolha em direções opostas — esconder
     * omite, e renderizar igual dá ao lixo a mesma autoridade.
     */
    montar([BUSCA]);

    expect(screen.getByRole("link", { name: /Revision f6538409/i })).not.toBeNull();
    expect(screen.queryByText(/não verificada/i)).not.toBeNull();
  });

  it("o site aparece junto do título", () => {
    /*
     * Sabotagem: mostrar só o título. "Edital do Enem 2026" sem o
     * veículo não diz se veio do GOV.BR ou de um blog qualquer.
     *
     * A asserção olha o VIZINHO do link, não a tela toda: o nome do
     * site também aparece na linha de resumo da ferramenta, e um
     * `queryByText` solto casava com os dois e falhava por ambiguidade.
     */
    montar([BUSCA]);
    const link = screen.getByRole("link", { name: /Edital do Enem/i });
    expect(link.parentElement?.textContent).toContain("Guia do Estudante");
  });

  it("ferramenta sem fontes não desenha lista", () => {
    /*
     * Sabotagem: renderizar a lista sempre.
     *
     * As outras 31 ferramentas passam pelo mesmo componente; uma lista
     * vazia embaixo de "lendo sua agenda" seria ruído em todo turno.
     */
    const { container } = montar([
      { name: "get_events", label: "lendo sua agenda", done: true, ok: true },
    ]);
    expect(container.querySelectorAll("a").length).toBe(0);
  });

  it("as fontes ficam dentro da caixa que recolhe", () => {
    /*
     * Sabotagem: desenhar as fontes fora do `<ul>` controlado pelo
     * botão.
     *
     * A caixa existe para a pessoa poder fechar; fontes soltas no meio
     * da conversa seriam exatamente o ruído que ela evita.
     */
    montar([BUSCA]);
    const botao = screen.getByRole("button", { expanded: true });

    expect(screen.queryByRole("link", { name: /Edital/i })).not.toBeNull();
    fireEvent.click(botao);
    expect(screen.queryByRole("link", { name: /Edital/i })).toBeNull();
  });
});
