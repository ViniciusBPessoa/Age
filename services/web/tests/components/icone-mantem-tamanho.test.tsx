/**
 * O icone nao perde o tamanho quando quem chama passa uma cor.
 *
 * `Svg` espalhava `{...rest}` DEPOIS do `className` padrao, entao
 * qualquer chamada com `className` substituia a string inteira e o
 * icone perdia `h-4 w-4 shrink-0`. Quem escrevia
 * `<IconTasks className="text-muted" />`, que parece inofensivo,
 * recebia um icone do tamanho natural do SVG.
 *
 * Na tela de Configuracoes isso aparecia como um icone enorme e
 * aparentemente duplicado ao lado de "Minhas tarefas", empurrando o
 * nome para duas linhas e inchando a linha de 48px para 90px.
 *
 * So foi visto na captura de tela. Nenhum teste olhava, e ler o
 * componente nao denunciava: a ordem do espalhamento e o tipo de
 * detalhe que o olho pula.
 */

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { IconCalendar, IconTasks } from "@/components/ui";

function classes(elemento: HTMLElement): string {
  const svg = elemento.querySelector("svg");
  if (svg === null) throw new Error("nenhum svg renderizado");
  return svg.getAttribute("class") ?? "";
}

describe("tamanho padrao do icone", () => {
  it("sem className, o padrao vale", () => {
    const { container } = render(<IconTasks />);
    expect(classes(container)).toContain("h-4 w-4");
  });

  it("com className de cor, o padrao SOBREVIVE", () => {
    // O caso exato do defeito.
    const { container } = render(<IconTasks className="text-muted" />);
    const cls = classes(container);

    expect(cls).toContain("h-4 w-4");
    expect(cls).toContain("text-muted");
  });

  it("shrink-0 tambem sobrevive", () => {
    // Sem ele o icone encolhe dentro de um flex apertado, que e outro
    // jeito de a linha sair errada.
    const { container } = render(<IconCalendar className="text-muted" />);
    expect(classes(container)).toContain("shrink-0");
  });

  it("quem pede outro tamanho continua podendo", () => {
    // Contrapeso: travar o tamanho resolveria o bug e quebraria os 64
    // lugares que passam o proprio.
    const { container } = render(<IconCalendar className="h-[18px] w-[18px]" />);
    expect(classes(container)).toContain("h-[18px]");
  });
});
