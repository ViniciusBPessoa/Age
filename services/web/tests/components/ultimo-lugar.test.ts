/**
 * Lembrar onde a pessoa estava (§8.1).
 *
 * Pedido: "clico em agenda, ele salva a última visualização que eu
 * tive; quando eu voltar para chat, ele está com o último chat que eu
 * estava aberto. Para eu não ter essa dificuldade maior de resetar o
 * que eu estava fazendo."
 *
 * O que estes testes protegem, além do óbvio:
 *
 * 1. O conteúdo de `localStorage` é entrada NÃO CONFIÁVEL. Sobrevive a
 *    deploys, pode ter sido escrito por uma versão antiga do app e é
 *    editável à mão no devtools. Um id inválido viraria `/chat/lixo`.
 * 2. A escrita pode LANÇAR em navegação privada, e perder a memória do
 *    último lugar não é motivo para a tela não abrir.
 * 3. A data da agenda NÃO é guardada, de propósito. Foi a escolha do
 *    usuário: lembrar dia/semana/mês e sempre abrir em hoje.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  esquecerUltimaConversa,
  guardarUltimaConversa,
  guardarUltimaVista,
  lerUltimaConversa,
  lerUltimaVista,
} from "@/lib/ultimo-lugar";

const UUID = "aaaaaaaa-1111-2222-3333-444444444444";

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("a última conversa", () => {
  it("volta igual ao que foi guardado", () => {
    guardarUltimaConversa(UUID);

    expect(lerUltimaConversa()).toBe(UUID);
  });

  it("sem nada guardado, não há conversa", () => {
    // O primeiro acesso de alguém. A navegação cai no `/chat` normal.
    expect(lerUltimaConversa()).toBeNull();
  });

  it("recusa valor que não é uuid", () => {
    /*
     * O caso que importa: um valor corrompido viraria o href
     * `/chat/nao-e-uuid` e uma tela de erro no lugar da conversa.
     * Melhor abrir a tela inicial do que uma tela quebrada.
     */
    localStorage.setItem("age-ultima-conversa", "../../admin");

    expect(lerUltimaConversa()).toBeNull();
  });

  it("recusa uuid truncado", () => {
    // Formato parecido mas inválido: o regex precisa exigir o tamanho
    // inteiro, não só os caracteres permitidos.
    localStorage.setItem("age-ultima-conversa", "aaaaaaaa-1111-2222");

    expect(lerUltimaConversa()).toBeNull();
  });

  it("esquecer apaga de verdade", () => {
    // Chamado ao apagar a conversa. Sem isto a navegação levaria a uma
    // conversa que não existe mais.
    guardarUltimaConversa(UUID);
    esquecerUltimaConversa();

    expect(lerUltimaConversa()).toBeNull();
  });

  it("leitura bloqueada não derruba a tela", () => {
    // Navegação privada pode lançar no acessor. A tela tem de abrir.
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("acesso negado");
    });

    expect(() => lerUltimaConversa()).not.toThrow();
    expect(lerUltimaConversa()).toBeNull();
  });

  it("escrita bloqueada não derruba a tela", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("cota cheia");
    });

    expect(() => guardarUltimaConversa(UUID)).not.toThrow();
  });
});

describe("a visualização da agenda", () => {
  it("volta igual para as três visualizações", () => {
    for (const vista of ["day", "week", "month"] as const) {
      guardarUltimaVista(vista);
      expect(lerUltimaVista()).toBe(vista);
    }
  });

  it("recusa visualização que não existe", () => {
    /*
     * `setViewState` recebe o valor direto. Um "ano" guardado por uma
     * versão futura, ou editado à mão, chegaria ao
     * `react-big-calendar` como visualização desconhecida.
     */
    localStorage.setItem("age-ultima-vista-agenda", "ano");

    expect(lerUltimaVista()).toBeNull();
  });

  it("não guarda a data em lugar nenhum", () => {
    /*
     * Decisão explícita do usuário quando perguntei, e é o tipo de
     * coisa que alguém "melhoraria" depois sem saber que foi escolhido:
     * guardar a data faria quem volta dias depois cair numa semana
     * velha sem perceber.
     *
     * O teste afirma sobre o armazenamento inteiro, não sobre uma
     * chave: assim pega também uma chave nova de data.
     */
    guardarUltimaVista("month");

    const chaves = Object.keys(localStorage);
    expect(chaves).toEqual(["age-ultima-vista-agenda"]);
  });
});
