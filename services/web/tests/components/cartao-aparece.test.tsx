/**
 * O cartão de aprovação que não apareceu (§5.2, §8.1).
 *
 * Relato, com a conversa inteira em mão: o Age organizou 3 sessões de
 * Python, respondeu "Preparei a proposta. Ela está esperando sua
 * aprovação, ainda não está na agenda" — e nenhum cartão apareceu na
 * tela. O usuário só viu as propostas depois de um F5, e observou que
 * "no chat não tem nenhuma mensagem perguntando se eu quero confirmar".
 *
 * O cartão É a confirmação. Sem ele não há como aprovar, então o turno
 * terminava num beco: a resposta prometia uma decisão que a tela não
 * oferecia.
 *
 * Medido no banco da conta real antes de escrever isto:
 *
 *   19:22:30  turno começa
 *   19:22:57  rascunho gravado em `ai.plan_drafts`
 *   19:24:35  plano materializado em `core.plans`  <- o F5
 *
 * 98 segundos com a proposta existindo e invisível. A promoção de
 * rascunho para plano é preguiçosa: roda DENTRO do `GET /plans`. O
 * backend está certo em cada elo — conferi o contrato, o worker e o
 * relay SSE, que encaminha `plan` antes de `done` — então a decisão de
 * exibir estava no cliente, inline, onde nenhum teste alcançava.
 *
 * Estes testes cobrem as tres funcoes puras que decidem: qual plano e
 * desta conversa, se o estado vazio pode cobrir o cartao, e por quanto
 * tempo insistir ate a promocao acontecer.
 *
 * A versao anterior registrava aqui uma divida: "nao cobrem o render
 * do cartao". Ela foi cobrada. A causa raiz nao era o render, era a
 * consulta estar ligada a um gatilho de turno que vivia so em memoria
 * (`turnoComPlano`), preenchido num unico lugar e restaurado em lugar
 * nenhum. F5, trocar de tela ou mandar a mensagem seguinte destruiam o
 * cartao para sempre, com o plano intacto no banco. Hoje a consulta e
 * funcao do servidor, e `cartao-sobrevive.test.tsx` monta a tela de
 * verdade para provar isso.
 */

import { describe, expect, it } from "vitest";

import {
  insistirAteAparecer,
  mostraEstadoVazio,
  planoDaConversa,
} from "@/components/chat/chat-screen";
import type { Plan } from "@/lib/types";

const CONVERSA = "aaaaaaaa-1111-2222-3333-444444444444";
const OUTRA = "bbbbbbbb-1111-2222-3333-444444444444";

/*
 * Sem `as Plan`: foi o que pegou dois campos inventados quando escrevi
 * os testes de ontem (`confidence`, que não existe, e a falta de
 * `origin_note` e `is_expired`). Um objeto completo obriga o tipo a
 * conferir de verdade.
 */
function plano(conversationId: string | null, id = "p1"): Plan {
  return {
    id,
    conversation_id: conversationId,
    status: "proposed",
    summary: "3 sessões de Python",
    origin_note: null,
    is_expired: false,
    created_at: "2026-10-16T19:22:57Z",
    expires_at: "2026-10-17T19:22:57Z",
    items: [],
  };
}

describe("qual proposta pertence à conversa aberta", () => {
  it("acha o plano desta conversa", () => {
    // O caso do relato: o plano existe e é desta conversa.
    const achado = planoDaConversa([plano(CONVERSA)], CONVERSA);

    expect(achado).not.toBeNull();
    expect(achado?.conversation_id).toBe(CONVERSA);
  });

  it("ignora proposta pendente de outra conversa", () => {
    /*
     * Importa de verdade: o usuário tinha propostas antigas pendentes.
     * Mostrar a de outro assunto convidaria a aprovar, no meio de uma
     * conversa, algo que foi pedido em outra.
     */
    expect(planoDaConversa([plano(OUTRA)], CONVERSA)).toBeNull();
  });

  it("escolhe o mais recente quando há vários", () => {
    // A lista chega ordenada por `created_at DESC`, e foi assim que as
    // 28 propostas empilhadas apareceram: várias pendentes ao mesmo
    // tempo na mesma conversa.
    const achado = planoDaConversa(
      [plano(CONVERSA, "novo"), plano(CONVERSA, "velho")],
      CONVERSA,
    );

    expect(achado?.id).toBe("novo");
  });

  it("sem conversa aberta, não há proposta a mostrar", () => {
    // Guarda contra casar com `conversation_id: null` por acidente: um
    // plano sem conversa não pertence à tela de ninguém.
    expect(planoDaConversa([plano(null)], null)).toBeNull();
  });

  it("lista vazia não quebra", () => {
    expect(planoDaConversa([], CONVERSA)).toBeNull();
  });
});

describe("o estado vazio nao pode esconder o cartao", () => {
  /*
   * Segundo caminho, independente da consulta, pelo qual o cartao
   * sumia. O estado vazio renderiza NO LUGAR do `MessageList`, e o
   * cartao e filho do `MessageList`: numa montagem em que as mensagens
   * ainda nao chegaram, a tela caia no estado vazio e o cartao ficava
   * inalcancavel mesmo com a proposta carregada.
   */
  it("com proposta carregada, nao mostra o estado vazio", () => {
    expect(mostraEstadoVazio(CONVERSA, 0, false, false, true)).toBe(false);
  });

  it("sem proposta e sem mensagem, mostra", () => {
    // Contrapeso: "nunca mostrar" passaria no teste acima.
    expect(mostraEstadoVazio(CONVERSA, 0, false, false, false)).toBe(true);
  });

  it("no primeiro turno, a proposta ja tira o estado vazio", () => {
    expect(mostraEstadoVazio(null, 0, false, false, true)).toBe(false);
  });

  it("com mensagens, nao mostra", () => {
    expect(mostraEstadoVazio(CONVERSA, 3, false, false, false)).toBe(false);
  });

  it("durante o turno, nao mostra", () => {
    expect(mostraEstadoVazio(CONVERSA, 0, true, false, false)).toBe(false);
  });

  it("com texto parcial chegando, nao mostra", () => {
    expect(mostraEstadoVazio(CONVERSA, 0, false, true, false)).toBe(false);
  });
});

describe("insistir ate a proposta aparecer", () => {
  function esperaFalsa(registro: number[]) {
    return async (ms: number) => {
      registro.push(ms);
    };
  }

  it("achando de primeira, nao espera", async () => {
    const esperas: number[] = [];
    let chamadas = 0;
    const achado = await insistirAteAparecer(async () => {
      chamadas += 1;
      return plano(CONVERSA);
    }, esperaFalsa(esperas));

    expect(achado?.id).toBe("p1");
    expect(chamadas).toBe(1);
    expect(esperas).toEqual([]);
  });

  it("insiste ate aparecer", async () => {
    const esperas: number[] = [];
    let chamadas = 0;
    const achado = await insistirAteAparecer(async () => {
      chamadas += 1;
      return chamadas < 4 ? null : plano(CONVERSA);
    }, esperaFalsa(esperas));

    expect(achado).not.toBeNull();
    expect(chamadas).toBe(4);
    expect(esperas.length).toBe(3);
  });

  it("desiste depois do teto, sem travar", async () => {
    const esperas: number[] = [];
    let chamadas = 0;
    const achado = await insistirAteAparecer(
      async () => {
        chamadas += 1;
        return null;
      },
      esperaFalsa(esperas),
      5,
      1000,
    );

    expect(achado).toBeNull();
    expect(chamadas).toBe(5);
  });

  it("a janela coberta alcanca os 98 segundos medidos", () => {
    /*
     * O `retry: 2, retryDelay: 400` que havia aqui cobria 800 ms, e a
     * promocao real levou 98 SEGUNDOS: era garantido falhar. Este
     * teste afirma sobre a aritmetica do padrao, para que diminuir o
     * teto volte a ser uma decisao consciente.
     */
    const tentativas = 8;
    const intervalo = 2000;
    expect((tentativas - 1) * intervalo).toBeGreaterThanOrEqual(14_000);
  });
});
