/**
 * O botão de pesquisa aprofundada no composer.
 *
 * Cada teste traz a sabotagem que precisa derrubá-lo.
 *
 * O que jsdom NÃO cobre, e eu confiro no navegador: se o botão cabe em
 * 390px sem empurrar o campo de texto, e se o estado ligado é visível
 * de relance. Teste verde não diz nada sobre aparência.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { type Mock, afterEach, describe, expect, it, vi } from "vitest";

import { Composer } from "@/components/chat/composer";
import type { ProviderInfo } from "@/lib/types";

afterEach(cleanup);

const COM_BUSCA: ProviderInfo = {
  key: "deepseek",
  name: "DeepSeek",
  models: ["deepseek-flash"],
  default_model: "deepseek-flash",
  vision_models: ["deepseek-flash"],
  web_search: true,
};

/** O `mock` do projeto: sem visão e sem busca. */
const SEM_BUSCA: ProviderInfo = {
  key: "mock",
  name: "Mock",
  models: ["mock-1"],
  default_model: "mock-1",
  vision_models: [],
  web_search: false,
};

function montar({
  providers = [COM_BUSCA],
  providerKey = "deepseek",
  model = "deepseek-flash",
  onSend = vi.fn(),
}: {
  providers?: ProviderInfo[];
  providerKey?: string | null;
  model?: string | null;
  onSend?: Mock;
} = {}) {
  render(
    <Composer
      providers={providers}
      credentials={[]}
      providerKey={providerKey}
      model={model}
      target="both"
      onSend={onSend}
      onProviderChange={vi.fn()}
      onTargetChange={vi.fn()}
    />,
  );
  return { onSend };
}

function botaoDeBusca() {
  return screen.queryByRole("button", {
    name: /pesquisa aprofundada na internet/i,
  });
}

function escrever(texto: string) {
  const campo = screen.getByRole("textbox");
  fireEvent.change(campo, { target: { value: texto } });
}

describe("quando o botao aparece", () => {
  it("aparece no provedor que tem busca", () => {
    montar();
    expect(botaoDeBusca()).not.toBeNull();
  });

  it("NAO aparece no provedor sem busca", () => {
    /*
     * Sabotagem: mostrar sempre.
     *
     * Um botão que não leva a lugar nenhum promete o que o modelo não
     * faz. É literalmente o defeito que aconteceu com o clipe: ele
     * apareceu para o `mock`, que não vê imagem, e só a captura da
     * tela mostrou.
     */
    montar({
      providers: [SEM_BUSCA],
      providerKey: "mock",
      model: "mock-1",
    });
    expect(botaoDeBusca()).toBeNull();
  });

  it("NAO aparece quando o catalogo omite o campo", () => {
    /*
     * Sabotagem: `web_search === undefined` virar `true`.
     *
     * Um worker anterior a este campo não o manda. Ausente tem de
     * significar "não tem" — e `Boolean(undefined)` é o que garante
     * isso. Se alguém trocar por `?? true`, o botão volta a aparecer
     * onde não funciona.
     */
    const semCampo: ProviderInfo = {
      key: "deepseek",
      name: "DeepSeek",
      models: ["deepseek-flash"],
      default_model: "deepseek-flash",
    };
    montar({ providers: [semCampo] });
    expect(botaoDeBusca()).toBeNull();
  });
});

describe("o estado do botao", () => {
  it("comeca desligado", () => {
    /*
     * Sabotagem: `useState(true)`.
     *
     * Ligado por padrão faria TODA mensagem ler o conteúdo das páginas
     * (~6.500 tokens contra ~1.200), sem ninguém ter pedido — e o
     * histórico relê isso a cada turno seguinte.
     */
    montar();
    expect(botaoDeBusca()?.getAttribute("aria-pressed")).toBe("false");
  });

  it("alterna no clique", () => {
    /*
     * Sabotagem: `<label>` + checkbox em vez de `aria-pressed` num
     * `<button>`.
     *
     * jsdom não propaga clique de label, então a versão com label
     * passaria este teste sem o botão funcionar de verdade na tela —
     * exatamente o que aconteceu no teste do clipe.
     */
    montar();
    const botao = botaoDeBusca()!;

    fireEvent.click(botao);
    expect(botao.getAttribute("aria-pressed")).toBe("true");

    fireEvent.click(botao);
    expect(botao.getAttribute("aria-pressed")).toBe("false");
  });

  it("o titulo explica o custo", () => {
    /*
     * Sabotagem: título genérico.
     *
     * A pessoa precisa saber que gasta mais ANTES de apertar. "Pesquisa
     * aprofundada" sozinho não diz isso.
     */
    montar();
    expect(botaoDeBusca()?.getAttribute("title")).toMatch(/gasta mais/i);

    fireEvent.click(botaoDeBusca()!);
    expect(botaoDeBusca()?.getAttribute("title")).toMatch(/ligada/i);
  });
});

describe("o que chega no envio", () => {
  it("manda `false` com o botao desligado", () => {
    /*
     * Sabotagem: mandar `true` sempre.
     *
     * O terceiro argumento é o que decide o orçamento no worker.
     * Ignorá-lo multiplicaria por 5 o custo de toda busca.
     */
    const { onSend } = montar();
    escrever("quando e o enem 2026?");
    fireEvent.click(screen.getByRole("button", { name: /enviar mensagem/i }));

    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend.mock.calls[0][2]).toBe(false);
  });

  it("manda `true` com o botao ligado", () => {
    /*
     * Sabotagem: nunca propagar a flag.
     *
     * Se o botão alterna mas o valor não viaja, a feature que o usuário
     * pediu não existe — e `aria-pressed` continuaria verde no teste
     * acima. Os dois só fecham juntos.
     */
    const { onSend } = montar();
    escrever("quando e o enem 2026?");
    fireEvent.click(botaoDeBusca()!);
    fireEvent.click(screen.getByRole("button", { name: /enviar mensagem/i }));

    expect(onSend.mock.calls[0][2]).toBe(true);
  });

  it("desliga depois de enviar", () => {
    /*
     * Sabotagem: manter ligado.
     *
     * A pesquisa aprofundada vale para a pergunta que a pessoa acabou
     * de fazer. Ficar ligada faria toda mensagem seguinte custar 5x
     * sem ela perceber — e o botão ligado no canto não é aviso
     * suficiente para um gasto recorrente.
     */
    const { onSend } = montar();
    escrever("primeira");
    fireEvent.click(botaoDeBusca()!);
    fireEvent.click(screen.getByRole("button", { name: /enviar mensagem/i }));

    expect(onSend.mock.calls[0][2]).toBe(true);
    expect(botaoDeBusca()?.getAttribute("aria-pressed")).toBe("false");
  });

  it("no provedor sem busca, o envio manda `false`", () => {
    /*
     * Sabotagem: o estado do botão sobreviver à troca de provedor.
     *
     * Ligar no DeepSeek e trocar para um provedor sem busca deixaria
     * `deep` ligado em memória. `deep && mostrarBusca` é o que impede
     * a flag de viajar para um worker que não pode atendê-la.
     */
    const { onSend } = montar({
      providers: [SEM_BUSCA],
      providerKey: "mock",
      model: "mock-1",
    });
    escrever("oi");
    fireEvent.click(screen.getByRole("button", { name: /enviar mensagem/i }));

    expect(onSend.mock.calls[0][2]).toBe(false);
  });
});

describe("acessibilidade", () => {
  it("tem nome acessivel, nao so o icone", () => {
    /*
     * Sabotagem: remover `aria-label`.
     *
     * Um `<button>` com só um `<svg>` dentro é um botão sem nome para
     * leitor de tela. §8.2 cobra isso, e o resto deste arquivo depende
     * do nome para achar o botão.
     */
    montar();
    expect(
      screen.getByRole("button", { name: /pesquisa aprofundada na internet/i }),
    ).not.toBeNull();
  });
});
