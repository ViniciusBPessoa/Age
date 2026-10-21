/**
 * O anexo no composer e na bolha da mensagem.
 *
 * Cada teste traz a sabotagem que precisa derrubá-lo.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  BotaoDeAnexo,
  MAXIMO_DE_ANEXOS,
  MiniaturasDeAnexo,
  type AnexoLocal,
} from "@/components/chat/anexos-do-composer";

afterEach(cleanup);

beforeEach(() => {
  // jsdom não implementa nenhum dos dois, e sem eles a prévia da
  // imagem explode antes de o componente renderizar.
  if (!URL.createObjectURL) {
    Object.defineProperty(URL, "createObjectURL", {
      value: vi.fn(() => "blob:fake"),
      writable: true,
    });
  }
  if (!URL.revokeObjectURL) {
    Object.defineProperty(URL, "revokeObjectURL", {
      value: vi.fn(),
      writable: true,
    });
  }
});

function anexo(over: Partial<AnexoLocal> = {}): AnexoLocal {
  return {
    localId: "l1",
    id: "s1",
    nome: "foto.png",
    previa: "blob:fake",
    estado: "pronto",
    ...over,
  };
}

describe("miniaturas no composer", () => {
  it("nao renderiza nada sem anexo", () => {
    /*
     * Sabotagem: devolver a `<ul>` vazia em vez de `null`. Uma lista
     * vazia ainda ocupa o padding do contêiner, e o composer ganharia
     * uma faixa em branco permanente acima do campo de texto.
     */
    const { container } = render(
      <MiniaturasDeAnexo anexos={[]} onRemover={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("mostra uma miniatura por anexo, com o nome no alt", () => {
    /*
     * Sabotagem: `alt=""` ou alt fixo. Quem usa leitor de tela precisa
     * saber QUAL imagem está anexada para poder remover a certa — e
     * `alt` vazio num `<img>` com ação ao lado é item invisível.
     */
    render(
      <MiniaturasDeAnexo
        anexos={[anexo(), anexo({ localId: "l2", nome: "quadro.jpg" })]}
        onRemover={() => {}}
      />,
    );
    expect(screen.getByAltText("foto.png")).toBeTruthy();
    expect(screen.getByAltText("quadro.jpg")).toBeTruthy();
  });

  it("o botao de remover diz o nome do arquivo", () => {
    /*
     * Sabotagem: `aria-label="Remover"` sem o nome. Com quatro
     * miniaturas, quatro botões "Remover" são indistinguíveis para
     * quem navega por teclado.
     */
    const remover = vi.fn();
    render(<MiniaturasDeAnexo anexos={[anexo()]} onRemover={remover} />);
    const botao = screen.getByRole("button", { name: "Remover foto.png" });
    fireEvent.click(botao);
    expect(remover).toHaveBeenCalledWith("l1");
  });

  it("o anexo em erro mostra o motivo em texto", () => {
    /*
     * Sabotagem: só a borda vermelha, sem o texto.
     *
     * "7.2 MB — o limite é 5 MB" é acionável: a pessoa sabe reduzir a
     * imagem. Uma borda colorida informa que algo falhou e não o quê,
     * e para quem não distingue cor não informa nada.
     */
    render(
      <MiniaturasDeAnexo
        anexos={[anexo({ estado: "erro", motivo: "7.2 MB — o limite é 5 MB" })]}
        onRemover={() => {}}
      />,
    );
    const aviso = screen.getByRole("status");
    expect(aviso.textContent).toContain("7.2 MB");
    expect(aviso.textContent).toContain("limite");
  });

  it("o anexo subindo fica visivelmente diferente do pronto", () => {
    /*
     * Sabotagem: mesma opacidade nos dois estados.
     *
     * Sem distinção, a pessoa clica em enviar achando que a imagem já
     * subiu — e o botão está desabilitado sem causa visível, o que
     * parece defeito.
     */
    const { container: subindo } = render(
      <MiniaturasDeAnexo
        anexos={[anexo({ estado: "enviando" })]}
        onRemover={() => {}}
      />,
    );
    const imgSubindo = subindo.querySelector("img")?.getAttribute("class") ?? "";
    cleanup();
    const { container: pronto } = render(
      <MiniaturasDeAnexo anexos={[anexo({ estado: "pronto" })]} onRemover={() => {}} />,
    );
    const imgPronto = pronto.querySelector("img")?.getAttribute("class") ?? "";

    expect(imgSubindo).not.toBe(imgPronto);
    expect(imgSubindo).toContain("opacity-40");
  });
});

describe("botao de anexo", () => {
  it("abre o seletor de arquivo e entrega os escolhidos", () => {
    /*
     * Sabotagem: ignorar `multiple`, ou entregar só o primeiro.
     * Comparar duas fotos exige mandar as duas no mesmo turno.
     */
    const escolher = vi.fn();
    const { container } = render(<BotaoDeAnexo onEscolher={escolher} />);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;

    expect(input.multiple).toBe(true);

    const arquivos = [
      new File([new Uint8Array([1])], "a.png", { type: "image/png" }),
      new File([new Uint8Array([2])], "b.jpg", { type: "image/jpeg" }),
    ];
    fireEvent.change(input, { target: { files: arquivos } });

    expect(escolher).toHaveBeenCalledTimes(1);
    expect(escolher.mock.calls[0][0]).toHaveLength(2);
  });

  it("oferece os quatro formatos que o provedor aceita", () => {
    /*
     * Sabotagem: `accept="image/*"`.
     *
     * O provedor aceita JPEG, PNG, GIF e WebP — `image/*` ofereceria
     * BMP, TIFF, HEIC e SVG, todos recusados depois do upload. HEIC é
     * o caso que dói: é o padrão do iPhone.
     */
    const { container } = render(<BotaoDeAnexo onEscolher={() => {}} />);
    const accept =
      container.querySelector('input[type="file"]')?.getAttribute("accept") ?? "";
    for (const tipo of ["image/jpeg", "image/png", "image/gif", "image/webp"]) {
      expect(accept).toContain(tipo);
    }
    expect(accept).not.toContain("image/*");
    // E o PDF, que entra pelo mesmo botão mas vira texto extraído.
    expect(accept).toContain("application/pdf");
  });

  it("desliga quando o limite de anexos foi atingido", () => {
    /*
     * Sabotagem: deixar o botão ligado e cortar só depois.
     *
     * Escolher uma quinta imagem e vê-la sumir sem explicação é pior
     * que o botão desligado com o motivo no `title`.
     */
    render(<BotaoDeAnexo onEscolher={() => {}} cheio />);
    const botao = screen.getByRole("button", { name: "Anexar imagem ou PDF" });
    expect(botao.hasAttribute("disabled")).toBe(true);
    expect(botao.getAttribute("title")).toContain(String(MAXIMO_DE_ANEXOS));
  });

  it("limpa o valor do input para permitir reescolher o MESMO arquivo", () => {
    /*
     * Sabotagem: remover a linha `e.target.value = ""`.
     *
     * Sem ela, remover uma foto e escolher a mesma de novo não dispara
     * `change` — o valor do input não mudou. O botão parece quebrado, e
     * é o caminho exato de quem anexou errado e corrige.
     *
     * Afirmo sobre o FONTE, não sobre `input.value` depois do evento:
     * jsdom devolve `""` para input de arquivo de qualquer jeito, então
     * a versão que eu escrevi primeiro passava com a linha removida.
     * Peguei na sabotagem — é o caso de
     * [[sabotagem-que-nao-pega]], e aqui o fonte é o único lugar onde a
     * diferença existe.
     */
    const fonte = readFileSync(
      join(process.cwd(), "components", "chat", "anexos-do-composer.tsx"),
      "utf8",
    );
    const trecho = fonte.slice(
      fonte.indexOf("onChange={(e) => {"),
      fonte.indexOf("</Button>"),
    );
    expect(trecho).toMatch(/e\.target\.value\s*=\s*""/);
  });
});
