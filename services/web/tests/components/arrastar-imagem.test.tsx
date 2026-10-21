/**
 * Arrastar imagem para a área da conversa.
 *
 * Cada teste traz a sabotagem que precisa derrubá-lo.
 *
 * O que jsdom NÃO cobre, e eu confiro no navegador: sem
 * `preventDefault` no `dragover`, um navegador real recusa a soltura e
 * `drop` nunca dispara — mas jsdom entrega o `drop` de qualquer jeito.
 * Um teste verde aqui não prova que funciona na tela.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  SobreposicaoDeArraste,
  imagensDe,
  temImagem,
  useArrastarImagem,
} from "@/components/chat/arrastar-imagem";

afterEach(cleanup);

function arquivo(nome: string, type: string) {
  return new File([new Uint8Array([1, 2, 3])], nome, { type });
}

/** Um `DataTransfer` suficiente: jsdom não implementa o de verdade. */
function dt(files: File[], types: string[] = ["Files"]) {
  return { files, types, dropEffect: "none" } as unknown as DataTransfer;
}

function Area({
  ativo = true,
  onSoltar = vi.fn(),
}: {
  ativo?: boolean;
  onSoltar?: (f: File[], ig: number) => void;
}) {
  const { sobre, props } = useArrastarImagem({ ativo, onSoltar });
  return (
    <main data-testid="area" {...props}>
      <div data-testid="filho">uma mensagem</div>
      <SobreposicaoDeArraste visivel={sobre} />
    </main>
  );
}

describe("filtro do que foi solto", () => {
  it("separa imagens do resto", () => {
    /*
     * Sabotagem: aceitar tudo. Um `.zip` subiria para ouvir não do
     * servidor — gasto de banda que no celular é dado móvel.
     */
    const r = imagensDe(
      dt([
        arquivo("a.png", "image/png"),
        arquivo("b.zip", "application/zip"),
        arquivo("c.jpg", "image/jpeg"),
      ]),
    );
    expect(r.imagens.map((f) => f.name)).toEqual(["a.png", "c.jpg"]);
    expect(r.ignorados).toBe(1);
  });

  it("PDF e aceito, nao ignorado", () => {
    /*
     * Sabotagem: tirar `application/pdf` de `TIPOS_ACEITOS` -- o que
     * ACONTECEU de verdade. O prettier colapsou o `Set` numa linha, a
     * minha edicao nao casou, e o PDF arrastado foi recusado com uma
     * frase que se contradizia: "so da para anexar imagem (...) e PDF".
     *
     * Os 314 testes continuaram verdes porque nenhum deles arrastava um
     * PDF. So a captura da tela mostrou.
     */
    const r = imagensDe(dt([arquivo("edital.pdf", "application/pdf")]));
    expect(r.imagens.map((f) => f.name)).toEqual(["edital.pdf"]);
    expect(r.ignorados).toBe(0);
  });

  it("separa PDF de imagem para a regra de visao", () => {
    /*
     * Sabotagem: `temImagem` devolver `true` sempre.
     *
     * PDF vira texto extraido e funciona em QUALQUER modelo; so a
     * imagem exige visao. Confundir os dois faria um PDF arrastado num
     * modelo sem visao ser recusado sem motivo.
     */
    expect(temImagem([arquivo("a.pdf", "application/pdf")])).toBe(false);
    expect(temImagem([arquivo("a.png", "image/png")])).toBe(true);
    expect(
      temImagem([arquivo("a.pdf", "application/pdf"), arquivo("b.png", "image/png")]),
    ).toBe(true);
  });

  it("conta os ignorados quando NADA e aceito", () => {
    /*
     * Sabotagem: devolver `ignorados: 0` sempre.
     *
     * Soltar um PDF e não acontecer nada parece defeito. É o contador
     * que permite dizer "por enquanto só imagem" em vez de ficar mudo
     * — e o PDF é justamente o que vem a seguir, então este caso vai
     * acontecer.
     */
    const r = imagensDe(dt([arquivo("pacote.zip", "application/zip")]));
    expect(r.imagens).toHaveLength(0);
    expect(r.ignorados).toBe(1);
  });

  it("texto arrastado nao vira anexo", () => {
    /*
     * Sabotagem: não checar `types`. Selecionar texto numa página e
     * arrastar para o chat dispara `drop` com `files` vazio; sem a
     * checagem, a sobreposição apareceria para seleção de texto.
     */
    const r = imagensDe(dt([], ["text/plain"]));
    expect(r.imagens).toHaveLength(0);
    expect(r.ignorados).toBe(0);
  });
});

describe("a zona de soltura", () => {
  it("mostra a sobreposicao ao entrar com arquivo", () => {
    render(<Area />);
    fireEvent.dragEnter(screen.getByTestId("area"), {
      dataTransfer: dt([arquivo("a.png", "image/png")]),
    });
    expect(screen.queryByText(/Solte o arquivo/i)).not.toBeNull();
  });

  it("NAO mostra a sobreposicao arrastando texto", () => {
    /*
     * Sabotagem: mostrar para qualquer arraste. Selecionar e arrastar
     * texto dentro da própria conversa cobriria a tela com "solte a
     * imagem aqui".
     */
    render(<Area />);
    fireEvent.dragEnter(screen.getByTestId("area"), {
      dataTransfer: dt([], ["text/plain"]),
    });
    expect(screen.queryByText(/Solte o arquivo/i)).toBeNull();
  });

  it("a sobreposicao NAO pisca ao cruzar elementos filhos", () => {
    /*
     * A sabotagem que este arquivo existe para pegar: trocar o
     * contador por um booleano (`setSobre(false)` direto no
     * `dragLeave`).
     *
     * `dragenter`/`dragleave` disparam a cada FILHO cruzado. Numa
     * conversa com dez mensagens, arrastar por cima dispara dezenas de
     * `dragleave` sem o cursor ter saído da área — e a sobreposição
     * pisca. O contador só zera quando entradas e saídas se igualam.
     */
    render(<Area />);
    const area = screen.getByTestId("area");
    const filho = screen.getByTestId("filho");
    const carga = { dataTransfer: dt([arquivo("a.png", "image/png")]) };

    fireEvent.dragEnter(area, carga); // entra na área
    fireEvent.dragEnter(filho, carga); // entra no filho (ainda dentro)
    fireEvent.dragLeave(area, carga); // sai do pai, por causa do filho

    expect(screen.queryByText(/Solte o arquivo/i)).not.toBeNull();
  });

  it("some quando o arraste realmente sai", () => {
    /*
     * O contrário do teste acima, e eles só fecham juntos: um
     * contador que nunca zera deixaria a sobreposição presa na tela.
     */
    render(<Area />);
    const area = screen.getByTestId("area");
    const carga = { dataTransfer: dt([arquivo("a.png", "image/png")]) };

    fireEvent.dragEnter(area, carga);
    fireEvent.dragLeave(area, carga);

    expect(screen.queryByText(/Solte o arquivo/i)).toBeNull();
  });

  it("entrega as imagens ao soltar, e some", () => {
    const onSoltar = vi.fn();
    render(<Area onSoltar={onSoltar} />);
    const area = screen.getByTestId("area");
    const carga = { dataTransfer: dt([arquivo("a.png", "image/png")]) };

    fireEvent.dragEnter(area, carga);
    fireEvent.drop(area, carga);

    expect(onSoltar).toHaveBeenCalledTimes(1);
    expect(onSoltar.mock.calls[0][0].map((f: File) => f.name)).toEqual(["a.png"]);
    expect(screen.queryByText(/Solte o arquivo/i)).toBeNull();
  });

  it("com o modelo sem visao, arrastar nao faz nada", () => {
    /*
     * Sabotagem: ignorar `ativo`.
     *
     * Aceitar a imagem para um modelo que não vê é a mesma promessa
     * quebrada que esconder o botão de anexo evita — com o agravante
     * de o upload já ter acontecido.
     */
    const onSoltar = vi.fn();
    render(<Area ativo={false} onSoltar={onSoltar} />);
    const area = screen.getByTestId("area");
    const carga = { dataTransfer: dt([arquivo("a.png", "image/png")]) };

    fireEvent.dragEnter(area, carga);
    expect(screen.queryByText(/Solte o arquivo/i)).toBeNull();

    fireEvent.drop(area, carga);
    expect(onSoltar).not.toHaveBeenCalled();
  });
});

describe("a sobreposicao", () => {
  it("nao intercepta o ponteiro", () => {
    /*
     * Sabotagem: remover `pointer-events-none`.
     *
     * Uma sobreposição que recebe eventos vira ela mesma o alvo do
     * arraste: dispara `dragleave` na `<main>`, e o `drop` se perde. O
     * sintoma é "a tela acende e a imagem não entra" — e jsdom não
     * simula isso, então é o atributo que precisa ser afirmado.
     */
    const { container } = render(<SobreposicaoDeArraste visivel />);
    expect(container.firstElementChild?.className).toContain("pointer-events-none");
  });

  it("avisa quando o limite ja foi atingido", () => {
    /*
     * Sabotagem: mostrar "solte aqui" sempre. A pessoa soltaria a
     * quinta imagem e ela sumiria sem explicação.
     */
    render(<SobreposicaoDeArraste visivel cheio />);
    expect(screen.queryByText(/Solte o arquivo/i)).toBeNull();
    expect(screen.queryByText(/4 anexos/i)).not.toBeNull();
  });

  it("é anunciada por leitor de tela", () => {
    /*
     * Sabotagem: remover `role="status"`/`aria-live`. Uma pista
     * puramente visual não existe para quem não enxerga.
     */
    render(<SobreposicaoDeArraste visivel />);
    const status = screen.getByRole("status");
    expect(status.getAttribute("aria-live")).toBe("polite");
  });

  it("invisivel nao renderiza nada", () => {
    const { container } = render(<SobreposicaoDeArraste visivel={false} />);
    expect(container.firstChild).toBeNull();
  });
});
