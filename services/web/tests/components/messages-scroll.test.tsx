/**
 * `<MessageList>` — a rolagem do histórico.
 *
 * Este arquivo existe por um bug que chegou ao usuário: numa conversa
 * com resposta longa, **não era possível rolar para cima**. O
 * histórico — e a parte de cima da própria resposta que estava sendo
 * lida — ficava inalcançável.
 *
 * A causa era `justify-end` no contêiner com `overflow-y-auto`. É um
 * comportamento conhecido do flexbox e não um detalhe de gosto: quando
 * o conteúdo passa da altura disponível, o alinhamento empurra o
 * INÍCIO do conteúdo para fora da área rolável, e o navegador não
 * permite rolar até lá. O `scrollTop` não chega ao começo porque, para
 * o layout, aquele espaço não existe.
 *
 * A intenção original era legítima — ancorar a conversa embaixo, junto
 * do compositor, para que duas mensagens não ficassem coladas no topo
 * com 600px de vazio. A correção preserva isso com `mt-auto` na lista:
 * sobra espaço, a margem empurra para baixo; falta espaço, ela colapsa
 * e a rolagem funciona.
 *
 * Por que testar CSS aqui, e não com navegador: o jsdom não calcula
 * layout, então não dá para medir `scrollHeight` de verdade. O que se
 * verifica é a propriedade que CAUSA o bug — a presença de
 * `justify-end` junto de `overflow-y-auto` no mesmo elemento. É uma
 * trava estreita, mas pega exatamente a regressão que aconteceu, e
 * roda em 200ms em vez de exigir Playwright.
 */

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MessageList } from "@/components/chat/messages";
import type { Message } from "@/lib/types";

const TZ = "America/Sao_Paulo";

/** Sem `as Message`: o cast escondeu que eu tinha inventado um campo
 * (`conversation_id`, que o tipo não tem) e omitido dois obrigatórios.
 * Deixar o compilador checar é o que torna o teste um contrato. */
function mensagem(i: number, role: "user" | "assistant"): Message {
  return {
    id: `m${i}`,
    role,
    // Texto longo de propósito: é o caso real em que o bug aparecia —
    // uma resposta do modelo que sozinha passa da altura da tela.
    content: `Mensagem ${i}. ${"palavra ".repeat(80)}`,
    provider_key: role === "assistant" ? "deepseek" : null,
    model: role === "assistant" ? "deepseek-flash" : null,
    created_at: new Date().toISOString(),
  };
}

function montar(quantas: number) {
  const messages = Array.from({ length: quantas }, (_, i) =>
    mensagem(i, i % 2 === 0 ? "user" : "assistant"),
  );
  return render(
    <MessageList
      messages={messages}
      streaming={false}
      streamingText=""
      traces={[]}
      timezone={TZ}
    />,
  );
}

/** O elemento que rola — o que tem `overflow-y-auto`. */
function rolavel(container: HTMLElement): HTMLElement {
  const el = container.querySelector<HTMLElement>(".overflow-y-auto");
  if (!el) throw new Error("não achei o contêiner rolável");
  return el;
}

describe("MessageList — a rolagem do histórico", () => {
  it("o contêiner rolável NÃO usa justify-end", () => {
    // A trava central. `justify-end` + `overflow-y-auto` no mesmo
    // elemento torna o topo do conteúdo inalcançável, e foi o bug
    // relatado: "não consigo rolar para cima no chat".
    const { container } = montar(12);
    const el = rolavel(container);

    expect(el.className).not.toMatch(/\bjustify-end\b/);
    expect(el.className).not.toMatch(/\bjustify-center\b/);
  });

  it("ancora a conversa embaixo por mt-auto na lista", () => {
    // A intenção que `justify-end` tinha continua valendo: com pouca
    // mensagem, a conversa fica junto do compositor, não colada no
    // topo com um vazio enorme embaixo.
    //
    // Verificado no elemento certo — na LISTA, não no contêiner: é o
    // que faz a margem colapsar quando falta espaço, em vez de cortar.
    const { container } = montar(2);
    const lista = container.querySelector("ul");

    expect(lista?.className).toMatch(/\bmt-auto\b/);
  });

  it("o contêiner continua rolável", () => {
    // Trava o outro lado: resolver o corte tirando a rolagem
    // (`overflow-hidden`) esconderia o problema em vez de corrigi-lo.
    const { container } = montar(12);
    expect(rolavel(container).className).toMatch(/\boverflow-y-auto\b/);
  });

  it("renderiza todas as mensagens, nenhuma virtualizada para fora", () => {
    // Se uma otimização futura cortar o histórico do DOM, rolar para
    // cima volta a não mostrar nada — mesmo sintoma, outra causa.
    const { container } = montar(12);
    expect(container.querySelectorAll("li").length).toBe(12);
  });
});
