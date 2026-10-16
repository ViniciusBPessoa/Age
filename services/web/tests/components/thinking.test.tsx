/**
 * O feedback de atividade durante o turno (§8.1).
 *
 * §8.1 pede que "as primeiras iterações de um turno são silenciosas
 * (§5.1), então a UI precisa mostrar progresso real, não um spinner".
 * O rastro de ferramentas cumpria metade disso, e a outra metade
 * faltava: havia dois vãos em que NADA se movia na tela.
 *
 * O relato foi "mostre quando a máquina estiver pensando, quando ela
 * estiver trazendo resposta, para evitar que o usuário fique só
 * esperando".
 *
 * Os dois vãos, medidos no uso real:
 *
 *  1. ferramenta rodando e nenhum texto ainda: aparecia só a linha da
 *     ferramenta, parada;
 *  2. entre a última ferramenta terminar e o primeiro token chegar —
 *     o vão MAIS LONGO, porque é o modelo redigindo a resposta inteira
 *     (medido em 2 a 8 segundos com o deepseek-flash).
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MessageList, type ToolTrace } from "@/components/chat/messages";

const TZ = "America/Sao_Paulo";

function tool(name: string, done: boolean): ToolTrace {
  return { name, label: `rodando ${name}`, done, ok: done ? true : undefined };
}

function montar(over: {
  streaming?: boolean;
  streamingText?: string;
  traces?: ToolTrace[];
}) {
  return render(
    <MessageList
      messages={[]}
      streaming={over.streaming ?? false}
      streamingText={over.streamingText ?? ""}
      traces={over.traces ?? []}
      timezone={TZ}
    />,
  );
}

describe("feedback de atividade no turno", () => {
  it("diz que está lendo o pedido no começo do turno", () => {
    // Antes de qualquer ferramenta: o turno começou e a tela precisa
    // dizer isso, senão o envio parece não ter funcionado.
    montar({ streaming: true });
    expect(screen.getByText("Lendo seu pedido")).toBeTruthy();
  });

  it("diz que está consultando enquanto a ferramenta roda", () => {
    // Vão 1. A condição antiga era `traces.length === 0`, então com
    // ferramenta em curso o indicador desaparecia e sobrava a linha da
    // tool, estática.
    montar({ streaming: true, traces: [tool("get_events", false)] });
    expect(screen.getByText("Consultando sua agenda")).toBeTruthy();
  });

  it("diz que está escrevendo quando as ferramentas acabaram", () => {
    // Vão 2, o mais longo e o que mais importa: todas as ferramentas
    // terminaram, o modelo está redigindo, e nenhum token saiu ainda.
    montar({
      streaming: true,
      traces: [tool("get_events", true), tool("get_routines", true)],
    });
    expect(screen.getByText("Escrevendo a resposta")).toBeTruthy();
  });

  it("ainda diz 'consultando' se alguma ferramenta não terminou", () => {
    // Uma pronta e outra em curso não é "redigindo": dizer que está
    // escrevendo quando ainda há consulta em curso seria mentir sobre
    // quanto falta.
    montar({
      streaming: true,
      traces: [tool("get_events", true), tool("get_routines", false)],
    });
    expect(screen.getByText("Consultando sua agenda")).toBeTruthy();
  });

  it("some assim que o texto começa a chegar", () => {
    // O texto é o melhor feedback que existe. Manter o indicador ao
    // lado dele seria ruído, e dizer "escrevendo" embaixo de texto já
    // escrito é redundante.
    montar({ streaming: true, streamingText: "Sua agenda está" });

    expect(screen.queryByText("Escrevendo a resposta")).toBeNull();
    expect(screen.queryByText("Lendo seu pedido")).toBeNull();
    expect(screen.getByText(/Sua agenda está/)).toBeTruthy();
  });

  it("não aparece fora de um turno", () => {
    // Conversa parada não tem indicador: ele significa "há trabalho em
    // curso", e exibi-lo sempre o tornaria mobília.
    montar({ streaming: false });
    expect(screen.queryByText("Lendo seu pedido")).toBeNull();
    expect(screen.queryByText("Escrevendo a resposta")).toBeNull();
  });

  it("é anunciado por leitor de tela, sem cortar a leitura (§8.2)", () => {
    // `polite` e não `assertive`: a troca de etapa não deve
    // interromper o que o leitor já estava falando.
    const { container } = montar({ streaming: true });
    const vivo = container.querySelector('[aria-live="polite"]');

    expect(vivo).toBeTruthy();
    expect(vivo?.textContent).toBe("Lendo seu pedido");
  });

  it("os pontos animados ficam escondidos do leitor de tela", () => {
    // Três bolinhas não têm conteúdo a anunciar; quem informa é o
    // texto ao lado.
    const { container } = montar({ streaming: true });
    const pontos = container.querySelectorAll("[aria-hidden] .animate-bounce");
    expect(pontos.length).toBe(3);
  });

  it("usa pontos em cascata, não spinner", () => {
    // Um spinner comunica "travado"; pontos em cascata comunicam "em
    // curso". A diferença de percepção é real e é a convenção do
    // formato.
    const { container } = montar({ streaming: true });
    expect(container.querySelectorAll(".animate-bounce").length).toBe(3);
    expect(container.querySelector(".animate-spin")).toBeNull();
  });
});

describe("o bloco do turno", () => {
  it("aparece assim que o turno começa", () => {
    // Sem isto, a mensagem do usuário fica sozinha na tela e parece
    // que o envio falhou.
    const { container } = montar({ streaming: true });
    expect(container.querySelector("li")).toBeTruthy();
  });
});
