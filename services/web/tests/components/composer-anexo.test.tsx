/**
 * O botão de anexo só existe quando o modelo escolhido vê imagem.
 *
 * Este arquivo nasceu de um defeito que só a CAPTURA DA TELA mostrou:
 * o botão aparecia ativo com "Mock (testes)" selecionado, e o mock não
 * vê imagem. O anexo subiria, seria gravado no banco, e o worker o
 * descartaria com um `log.warning` — cada camada certa sozinha, e a
 * promessa quebrada no meio.
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Composer } from "@/components/chat/composer";
import type { Credential, ProviderInfo } from "@/lib/types";

afterEach(cleanup);

const DEEPSEEK: ProviderInfo = {
  key: "deepseek",
  name: "DeepSeek",
  models: ["deepseek-flash", "deepseek-v4-pro"],
  default_model: "deepseek-flash",
  vision_models: ["deepseek-flash", "deepseek-v4-pro"],
};

const MOCK: ProviderInfo = {
  key: "mock",
  name: "Mock (testes)",
  models: ["mock-chat"],
  default_model: "mock-chat",
  vision_models: [],
};

function credencial(provider_key: string): Credential {
  return {
    id: `c-${provider_key}`,
    provider_key,
    key_last4: "1234",
    default_model: null,
    base_url: null,
    label: null,
    is_active: true,
    last_error: null,
    last_verified_at: null,
    created_at: new Date().toISOString(),
  };
}

function montar(over: Partial<React.ComponentProps<typeof Composer>> = {}) {
  return render(
    <Composer
      providers={[DEEPSEEK, MOCK]}
      credentials={[credencial("deepseek"), credencial("mock")]}
      providerKey="deepseek"
      model="deepseek-flash"
      target="both"
      onSend={vi.fn()}
      onProviderChange={vi.fn()}
      onTargetChange={vi.fn()}
      {...over}
    />,
  );
}

describe("botao de anexo no composer", () => {
  it("aparece quando o modelo escolhido ve imagem", () => {
    /*
     * Sabotagem: esconder sempre. O recurso inteiro ficaria
     * inalcançável, e nenhum teste de backend notaria.
     */
    montar();
    expect(screen.queryByLabelText("Anexar imagem")).not.toBeNull();
  });

  it("NAO aparece com um modelo que nao ve imagem", () => {
    /*
     * Sabotagem: renderizar o botão sempre — o estado que a captura
     * pegou. O anexo subiria e seria descartado no worker.
     */
    montar({ providerKey: "mock", model: "mock-chat" });
    expect(screen.queryByLabelText("Anexar imagem")).toBeNull();
  });

  it("NAO aparece quando o backend nao informa quais modelos veem", () => {
    /*
     * Sabotagem: `?? models` em vez de `?? []`.
     *
     * Um backend anterior a este campo não manda `vision_models`.
     * Tratar ausência como "todos veem" ofereceria anexo para
     * qualquer modelo — o default inseguro. Ausência é "não sei", e
     * não sei esconde.
     */
    const semCampo: ProviderInfo = {
      key: "deepseek",
      name: "DeepSeek",
      models: ["deepseek-flash"],
      default_model: "deepseek-flash",
    };
    montar({ providers: [semCampo], model: "deepseek-flash" });
    expect(screen.queryByLabelText("Anexar imagem")).toBeNull();
  });

  it("um modelo sem visao no MESMO provedor nao ganha o botao", () => {
    /*
     * Sabotagem: olhar só o provedor (`vision_models.length > 0`) e
     * não o modelo escolhido.
     *
     * É a sabotagem que os testes acima NÃO pegam: um provedor com um
     * modelo que vê e outro que não veria o botão nos dois.
     */
    const misto: ProviderInfo = {
      key: "deepseek",
      name: "DeepSeek",
      models: ["deepseek-flash", "deepseek-sem-visao"],
      default_model: "deepseek-flash",
      vision_models: ["deepseek-flash"],
    };
    montar({ providers: [misto], model: "deepseek-sem-visao" });
    expect(screen.queryByLabelText("Anexar imagem")).toBeNull();
  });
});

describe("conta que usa a chave da instancia", () => {
  /*
   * Esta conta NAO tem credencial propria: `credentials` vem vazio e o
   * seletor fica em "::" -- e e o caminho padrao do Age, nao a
   * excecao. Os dois testes abaixo vieram da captura da tela, nao de
   * raciocinio: o botao sumia e o rotulo mentia para a maioria dos
   * usuarios.
   */

  it("mostra o botao de anexo mesmo sem credencial propria", () => {
    /*
     * Sabotagem: voltar `providerKey`/`model` a depender so de
     * `credentials[0]`. O anexo ficaria inalcancavel para quem usa o
     * Age sem chave propria, e TODOS os outros testes continuariam
     * passando, porque eles passam `providerKey` explicito.
     */
    montar({ credentials: [], providerKey: "deepseek", model: "deepseek-flash" });
    expect(screen.queryByLabelText("Anexar imagem")).not.toBeNull();
  });

  it("o seletor nao diz 'sem credencial' quando ha modelo emprestado", () => {
    /*
     * Sabotagem: voltar ao texto fixo.
     *
     * "sem credencial" ao lado de um chat que funciona e de um botao
     * de anexo ativo contradiz o que a tela faz -- o mesmo tipo de
     * texto que se contradiz que eu corrigi em Configuracoes.
     */
    montar({ credentials: [], providerKey: "deepseek", model: "deepseek-flash" });
    // Dentro do seletor de PROVEDOR: o de destino tambem tem <option>.
    const seletor = document.getElementById("provider-select") as HTMLSelectElement;
    const texto = seletor.textContent ?? "";
    expect(texto).toContain("deepseek-flash");
    expect(texto).not.toContain("sem credencial");
  });

  it("sem credencial E sem instancia, o texto antigo vale", () => {
    /*
     * Sabotagem: mostrar o modelo sempre. Com nada configurado, nao ha
     * modelo nenhum a nomear, e "sem credencial" e a verdade.
     */
    montar({ credentials: [], providerKey: null, model: null });
    const seletor = document.getElementById("provider-select") as HTMLSelectElement;
    expect(seletor.textContent ?? "").toContain("sem credencial");
  });
});
