/**
 * Os tokens de cor precisam aceitar opacidade.
 *
 * Isto nasceu de uma medicao no navegador, nao de leitura de codigo. O
 * `<select>` de fuso horario, com foco, devolvia:
 *
 *   boxShadow: rgba(59, 130, 246, 0.5) 0px 0px 0px 3px
 *
 * `rgb(59,130,246)` e o azul PADRAO do Tailwind, nao a marca. O codigo
 * pedia `focus:ring-brand/20` e recebia azul generico a 50%.
 *
 * A causa: as cores do `tailwind.config.ts` eram string crua,
 * `brand: "var(--brand)"`. O Tailwind so injeta canal alfa quando a cor
 * traz o marcador `<alpha-value>`; sem ele todo modificador
 * `/opacidade` e DESCARTADO em silencio e o utilitario cai no default.
 *
 * Eram 85 utilitarios assim, em 16 arquivos: toda tarja de erro
 * (`bg-danger/10`), todo hover do menu (`bg-surface-2/60`), e o anel de
 * foco de todo campo. Metade do relato "quando voce clica ele fica
 * esquisito, nao tem a mesma formatacao que o restante da pagina".
 *
 * O conserto tem duas metades que SO funcionam juntas, e por isso as
 * duas sao testadas aqui:
 *
 *   1. o token guarda canais (`--brand: 99 136 255`), nao hex;
 *   2. a cor do config envolve o token em `rgb(... / <alpha-value>)`.
 *
 * Fazer so a (2) pinta tudo de preto, porque `rgb(#6388ff / 0.2)` nao e
 * cor valida. Fazer so a (1) deixa o bug de pe. Um teste que olhasse
 * apenas uma das metades passaria com a tela quebrada.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const WEB_ROOT = join(__dirname, "..");

const CSS = readFileSync(join(WEB_ROOT, "app", "globals.css"), "utf8");
const CONFIG = readFileSync(join(WEB_ROOT, "tailwind.config.ts"), "utf8");

/**
 * As cores do `theme.extend.colors`, com o valor que cada uma declara.
 *
 * Devolve o PAR, e nao so o nome, por causa de um buraco que a
 * sabotagem encontrou e a leitura nao: a primeira versao procurava o
 * valor por nome no arquivo inteiro, e `brand` tambem existe em
 * `boxShadow` (uma sombra, que legitimamente contem `rgb(...)`). Com
 * isso, sabotar a cor `brand` nao derrubava teste nenhum, porque a
 * busca encontrava a sombra e dava o nome por bom.
 */
function coresDoConfig(): Map<string, string> {
  const bloco = CONFIG.match(/colors:\s*\{([\s\S]*?)\n\s{6}\}/);
  if (bloco === null) throw new Error("bloco de cores nao encontrado");
  const achadas = new Map<string, string>();
  for (const linha of bloco[1].matchAll(/^\s*"?([a-z0-9-]+)"?:\s*(.+)$/gm)) {
    achadas.set(linha[1], linha[2]);
  }
  return achadas;
}

describe("todo token de cor guarda canais, nunca hex", () => {
  it("nenhuma declaracao de cor usa hexadecimal", () => {
    /*
     * Procura `--algo: #rrggbb`. Se sobrar um, o `<alpha-value>` do
     * config transforma aquele token em cor invalida, e tudo que o usa
     * some da tela. E o jeito mais caro de errar esta mudanca.
     */
    const hex = [...CSS.matchAll(/^\s*(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{3,8})/gm)];
    expect(hex.map((m) => m[1] + ": " + m[2])).toEqual([]);
  });

  it("os tres blocos de tema declaram os tokens em canais", () => {
    // Escuro, claro por preferencia do sistema, claro por escolha.
    const canais = [...CSS.matchAll(/^\s*--brand:\s*\d+\s+\d+\s+\d+\s*;/gm)];
    expect(canais.length).toBe(3);
  });
});

describe("toda cor do config aceita opacidade", () => {
  it("cada cor envolve o token com rgb(... / <alpha-value>)", () => {
    const semAlpha: string[] = [];
    for (const [nome, valor] of coresDoConfig()) {
      if (!valor.includes("<alpha-value>")) semAlpha.push(nome);
    }
    expect(semAlpha).toEqual([]);
  });

  it("o config declara as cores que a tela usa", () => {
    // Contrapeso: sem isto, apagar o bloco de cores inteiro passaria,
    // porque "nenhuma cor sem alpha" e verdade quando nao ha cor.
    const nomes = [...coresDoConfig().keys()];
    for (const esperada of ["brand", "danger", "ok", "warn", "surface", "ink"]) {
      expect(nomes).toContain(esperada);
    }
  });
});

describe("as referencias em CSS acompanham o formato", () => {
  it("nenhum var() de cor aparece fora de um rgb()", () => {
    /*
     * Com o token em canais, `color: var(--ink)` vira `color: 240 242
     * 246`, que nao e cor. Cada uso direto precisa de `rgb(...)` em
     * volta. Vale para `globals.css` e para o CSS da timeline, que e
     * onde estavam 44 das 58 referencias.
     */
    /*
     * Regex LITERAL, nao montada por string.
     *
     * A primeira versao montava com `new RegExp("var\(--...")`, e o
     * heredoc que escreveu o arquivo comeu um nivel de barra. Sobrou
     * `var(--`, que em string JS e so `var(--`: um grupo aberto que
     * casava com coisa nenhuma. O teste passava com zero achados,
     * inclusive com a sabotagem aplicada. Literal nao tem essa classe
     * de erro, porque o que esta escrito e o que o motor ve.
     */
    const PROCURA =
      /(.{0,4})var\(--(canvas|surface-2|surface-3|surface|line-strong|line|ink-2|ink|muted|faint|brand-soft|brand-ink|brand|ok|warn|danger|fixed|flexible|proposed)\)/g;
    const crus: string[] = [];
    for (const arquivo of [
      join("app", "globals.css"),
      join("components", "calendar-timeline", "timeline.css"),
    ]) {
      const texto = readFileSync(join(WEB_ROOT, arquivo), "utf8");
      for (const achado of texto.matchAll(PROCURA)) {
        if (!achado[1].endsWith("rgb(")) {
          crus.push(arquivo + ": var(--" + achado[2] + ")");
        }
      }
    }
    expect(crus).toEqual([]);
  });
});
