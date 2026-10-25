/**
 * O rascunho que não some (§8.1).
 *
 * Cada teste traz a sabotagem que precisa derrubá-lo.
 *
 * O relato: *"quando eu saí do chat para ver meu calendário, eu voltei
 * e ele apagou a mensagem que estava escrita lá"*. O campo era
 * `useState("")` puro: sair da tela desmontava o componente e o texto
 * morria junto.
 *
 * O pedido completo incluía a marca na lista: *"fica uma indicaçãozinha
 * visual de que tem uma mensagem a ser enviada"*.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Composer } from "@/components/chat/composer";
import {
  conversasComRascunho,
  lerRascunho,
  limparRascunho,
  migrarRascunho,
  salvarRascunho,
} from "@/lib/rascunho";
import type { ProviderInfo } from "@/lib/types";

const PROVEDOR: ProviderInfo = {
  key: "deepseek",
  name: "DeepSeek",
  models: ["deepseek-flash"],
  default_model: "deepseek-flash",
  vision_models: ["deepseek-flash"],
  web_search: true,
};

beforeEach(() => {
  window.localStorage.clear();
});
afterEach(cleanup);

function montar(conversaId: string | null, onSend = vi.fn()) {
  render(
    <Composer
      conversaId={conversaId}
      providers={[PROVEDOR]}
      credentials={[]}
      providerKey="deepseek"
      model="deepseek-flash"
      target="both"
      onSend={onSend}
      onProviderChange={vi.fn()}
      onTargetChange={vi.fn()}
    />,
  );
  return { onSend };
}

/** O campo de texto, tipado: este projeto não usa jest-dom. */
function campo(): HTMLTextAreaElement {
  return screen.getByRole("textbox") as HTMLTextAreaElement;
}

function escrever(texto: string) {
  fireEvent.change(screen.getByRole("textbox"), {
    target: { value: texto },
  });
}

describe("o armazenamento do rascunho", () => {
  it("guarda e devolve por conversa", () => {
    /*
     * A sabotagem que só este teste pega: usar UMA chave para tudo.
     *
     * O texto de um chat apareceria no outro — pior que perder o
     * rascunho, porque a pessoa poderia enviar para a conversa errada.
     */
    salvarRascunho("conversa-a", "texto da A");
    salvarRascunho("conversa-b", "texto da B");

    expect(lerRascunho("conversa-a")).toBe("texto da A");
    expect(lerRascunho("conversa-b")).toBe("texto da B");
  });

  it("texto em branco não conta como rascunho", () => {
    /*
     * Sabotagem: guardar qualquer string.
     *
     * Espaços sobrando marcariam a conversa como "tem mensagem não
     * enviada" — um ponto que nunca apaga e que a pessoa aprende a
     * ignorar.
     */
    salvarRascunho("c1", "   \n  ");
    expect(conversasComRascunho().has("c1")).toBe(false);
  });

  it("lista as conversas com texto pendente", () => {
    // Sabotagem: devolver tudo, inclusive as vazias.
    salvarRascunho("c1", "oi");
    salvarRascunho("c2", "");
    salvarRascunho("c3", "tudo bem?");

    const ids = conversasComRascunho();
    expect(ids.has("c1")).toBe(true);
    expect(ids.has("c2")).toBe(false);
    expect(ids.has("c3")).toBe(true);
  });

  it("migra o rascunho da conversa nova para o id real", () => {
    /*
     * Sabotagem: não migrar.
     *
     * A primeira mensagem é escrita antes de a conversa existir. Sem
     * isto o texto fica preso na chave `nova`, e a marca de pendente
     * aparece na conversa errada para sempre.
     */
    salvarRascunho(null, "primeira mensagem");
    migrarRascunho("conversa-nova");

    expect(lerRascunho("conversa-nova")).toBe("primeira mensagem");
    expect(lerRascunho(null)).toBe("");
  });

  it("não quebra quando o armazenamento recusa", () => {
    /*
     * A sabotagem: tirar o `try/catch`.
     *
     * Em aba anônima, com cookies de terceiros bloqueados ou cota
     * estourada, o acesso LANÇA. Um chat que não abre por causa de
     * rascunho é muito pior que um rascunho perdido.
     */
    const original = window.localStorage.getItem;
    window.localStorage.getItem = () => {
      throw new Error("acesso negado");
    };
    try {
      expect(lerRascunho("c1")).toBe("");
      expect(() => conversasComRascunho()).not.toThrow();
    } finally {
      window.localStorage.getItem = original;
    }
  });
});

describe("o composer e o rascunho", () => {
  it("restaura o texto ao montar", () => {
    // Sabotagem: nunca ler o rascunho. É o defeito original.
    salvarRascunho("c1", "estava escrevendo isto");
    montar("c1");

    expect(campo().value).toBe("estava escrevendo isto");
  });

  it("guarda o que foi digitado", async () => {
    // Sabotagem: não salvar. O texto some ao trocar de tela.
    vi.useFakeTimers();
    try {
      montar("c1");
      escrever("mensagem a caminho");
      await vi.advanceTimersByTimeAsync(600);
      expect(lerRascunho("c1")).toBe("mensagem a caminho");
    } finally {
      vi.useRealTimers();
    }
  });

  it("apaga o rascunho ao enviar", async () => {
    /*
     * Sabotagem: deixar o rascunho depois do envio.
     *
     * A conversa ficaria marcada como "tem mensagem não enviada" logo
     * depois de enviar — e a marca perderia o sentido.
     */
    vi.useFakeTimers();
    try {
      montar("c1");
      escrever("vai agora");
      await vi.advanceTimersByTimeAsync(600);
      expect(lerRascunho("c1")).toBe("vai agora");

      fireEvent.click(screen.getByRole("button", { name: /enviar mensagem/i }));
      expect(lerRascunho("c1")).toBe("");
    } finally {
      vi.useRealTimers();
    }
  });

  it("cada conversa mostra o SEU texto", () => {
    /*
     * Sabotagem: não recarregar ao trocar de conversa (dependência
     * faltando no efeito).
     *
     * Abrir outro chat mostraria o texto do anterior, e enviar mandaria
     * para o lugar errado.
     */
    salvarRascunho("c1", "texto da um");
    salvarRascunho("c2", "texto da dois");

    montar("c1");
    expect(campo().value).toBe("texto da um");

    cleanup();
    montar("c2");
    expect(campo().value).toBe("texto da dois");
  });

  it("conversa sem rascunho abre vazia", () => {
    // Sabotagem: cair na chave `nova` quando a conversa tem id.
    salvarRascunho(null, "texto da conversa nova");
    montar("c1");
    expect(campo().value).toBe("");
  });
});

describe("limpar", () => {
  it("remove o rascunho da conversa", () => {
    salvarRascunho("c1", "algo");
    limparRascunho("c1");
    expect(lerRascunho("c1")).toBe("");
    expect(conversasComRascunho().has("c1")).toBe(false);
  });
});
