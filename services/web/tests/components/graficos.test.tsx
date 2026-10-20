/**
 * Os graficos SVG, verificados pela arvore e nao por pixel.
 *
 * jsdom nao faz layout: `getBoundingClientRect()` devolve zero em
 * tudo. Por isso os componentes usam `viewBox` em unidades proprias em
 * vez de medir o container -- e por isso aqui da para afirmar sobre
 * `height` e `width` dos `<rect>`, que sao atributos do SVG e nao
 * resultado de layout.
 *
 * Cada teste traz a sabotagem que precisa derruba-lo.
 */

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { BarChart } from "@/components/charts/bar-chart";
import { StackedBar } from "@/components/charts/stacked-bar";

afterEach(cleanup);

function barras(valores: number[]) {
  return valores.map((v, i) => ({ label: `d${i}`, value: v }));
}

function alturaDe(c: HTMLElement, label: string): number {
  const el = c.querySelector(`[data-barra="${label}"]`);
  return Number(el?.getAttribute("height"));
}

describe("grafico de barras", () => {
  it("desenha uma barra por ponto, inclusive os zerados", () => {
    /*
     * Sabotagem: filtrar `bars.filter(b => b.value > 0)` -- o
     * "otimizador" obvio. Sobrariam 4 barras de 7, e o grafico
     * comprimiria os vaos: um fim de semana parado pareceria nao ter
     * existido.
     */
    const { container } = render(
      <BarChart
        bars={barras([0, 100, 500, 0, 1000, 50, 0])}
        ariaLabel="uso por dia"
        caption="uso"
      />,
    );

    expect(container.querySelectorAll("[data-barra]")).toHaveLength(7);
  });

  it("a altura e proporcional ao valor", () => {
    /*
     * Sabotagem: altura fixa em todas as barras.
     *
     * Esta e a assercao que a contagem sozinha NAO pega: um componente
     * que desenhasse 7 retangulos identicos passaria no teste de cima
     * e seria um grafico inutil.
     */
    const { container } = render(
      <BarChart
        bars={barras([0, 100, 500, 0, 1000, 50, 0])}
        ariaLabel="uso por dia"
        caption="uso"
      />,
    );

    const h = (l: string) => alturaDe(container, l);
    expect(h("d4")).toBeGreaterThan(h("d2"));
    expect(h("d2")).toBeGreaterThan(h("d1"));
    expect(h("d1")).toBeGreaterThan(h("d5"));
    // O maior ocupa a altura util inteira.
    expect(h("d4")).toBe(100);
  });

  it("valor pequeno continua visivel", () => {
    /*
     * Sabotagem: remover o `Math.max(1, ...)`.
     *
     * 50 numa escala de 500 mil da 0,01 de altura, que arredonda para
     * nada. "Nao usei" e "usei pouco" viram a mesma imagem -- a pior
     * confusao possivel num grafico de uso.
     */
    const { container } = render(
      <BarChart bars={barras([500_000, 50])} ariaLabel="uso" caption="uso" />,
    );

    expect(alturaDe(container, "d1")).toBeGreaterThanOrEqual(1);
  });

  it("zero vira traco de base, nao barra", () => {
    /*
     * O contrario do teste de cima, e eles so fecham juntos.
     *
     * Altura zero sumia no escuro, e um grafico onde o dia vazio e
     * invisivel nao distingue "nao usei" de "o periodo acabou aqui" --
     * vi isso na captura. O traco de base diz que o dia existe.
     *
     * Sabotagem em qualquer direcao: dar ao zero a mesma altura de um
     * valor pequeno (afirmaria uso que nao houve) ou voltar a zero
     * (some). O limite de 1/10 da barra mais baixa separa os dois.
     */
    const { container } = render(
      <BarChart bars={barras([100, 0])} ariaLabel="uso" caption="uso" />,
    );

    const vazio = alturaDe(container, "d1");
    const cheio = alturaDe(container, "d0");
    expect(vazio).toBeGreaterThan(0);
    expect(vazio).toBeLessThan(cheio / 10);
  });

  it("serie toda zerada nao quebra", () => {
    /*
     * Sabotagem: dividir por `maximo` sem proteger o zero -- daria
     * `NaN` em todo atributo `height`, e o SVG renderiza vazio sem
     * erro nenhum no console.
     *
     * E o estado de quem abre o painel no primeiro dia.
     */
    const { container } = render(
      <BarChart bars={barras([0, 0, 0])} ariaLabel="uso" caption="uso" />,
    );

    const alturas = [...container.querySelectorAll("[data-barra]")].map((el) =>
      Number(el.getAttribute("height")),
    );
    // Todas iguais (o traco de base), e nenhuma `NaN`.
    expect(new Set(alturas).size).toBe(1);
    expect(alturas.every((a) => !Number.isNaN(a))).toBe(true);
    expect(alturas.every((a) => a < 1)).toBe(true);
  });

  it("e legivel por leitor de tela", () => {
    /*
     * Sabotagem: remover `role="img"`, o `aria-label` ou a tabela.
     * Um `<svg>` sem isso e invisivel para quem nao enxerga, e secao
     * 8.2 e testada em `architecture.test.ts`.
     */
    const { container, getByRole } = render(
      <BarChart
        bars={barras([10, 20])}
        ariaLabel="tokens por dia, 1 a 2 de outubro"
        caption="tokens por dia"
      />,
    );

    expect(getByRole("img").getAttribute("aria-label")).toBe(
      "tokens por dia, 1 a 2 de outubro",
    );
    const tabela = container.querySelector("table");
    expect(tabela).not.toBeNull();
    expect(tabela?.querySelectorAll("tr")).toHaveLength(2);
    expect(tabela?.textContent).toContain("tokens por dia");
  });

  it("o tooltip diz o valor formatado", () => {
    /*
     * Sabotagem: ignorar `formatValue` e imprimir o numero cru. "2848884"
     * nao e legivel; "2,8M" e.
     */
    const { container } = render(
      <BarChart
        bars={[{ label: "d0", value: 2_848_884 }]}
        ariaLabel="uso"
        caption="uso"
        formatValue={(v) => `${(v / 1e6).toFixed(1)}M`}
      />,
    );

    expect(container.querySelector("title")?.textContent).toContain("2.8M");
  });
});

describe("barra empilhada", () => {
  it("os segmentos somam 100%", () => {
    /*
     * Sabotagem: dividir pelo MAIOR valor em vez da soma. Com
     * [600,300,100] daria 100+50+16,7 = 166,7 -- a barra transbordaria
     * sem erro nenhum.
     */
    const { container } = render(
      <StackedBar
        slices={[
          { label: "a", value: 600 },
          { label: "b", value: 300 },
          { label: "c", value: 100 },
        ]}
        ariaLabel="por modelo"
      />,
    );

    const larguras = [...container.querySelectorAll("[data-fatia]")].map((el) =>
      Number(el.getAttribute("data-largura")),
    );
    expect(larguras).toEqual([60, 30, 10]);
    expect(larguras.reduce((s, l) => s + l, 0)).toBeCloseTo(100, 1);
  });

  it("os segmentos ficam lado a lado, sem vao nem sobreposicao", () => {
    /*
     * Sabotagem: `x` fixo em 0 -- todos empilhados no mesmo lugar,
     * mostrando so o ultimo. As larguras continuariam somando 100 e o
     * teste de cima passaria.
     */
    const { container } = render(
      <StackedBar
        slices={[
          { label: "a", value: 600 },
          { label: "b", value: 300 },
          { label: "c", value: 100 },
        ]}
        ariaLabel="por modelo"
      />,
    );

    const segs = [...container.querySelectorAll("[data-fatia]")].map((el) => ({
      x: Number(el.getAttribute("x")),
      w: Number(el.getAttribute("width")),
    }));
    for (let i = 1; i < segs.length; i++) {
      expect(segs[i].x).toBeCloseTo(segs[i - 1].x + segs[i - 1].w, 5);
    }
  });

  it("total zero nao renderiza nada", () => {
    /*
     * Sabotagem: dividir sem checar -- `0/0` e `NaN`, e o SVG sai com
     * atributos invalidos em vez de nao sair.
     */
    const { container } = render(
      <StackedBar slices={[{ label: "a", value: 0 }]} ariaLabel="por modelo" />,
    );

    expect(container.querySelector("svg")).toBeNull();
  });
});
