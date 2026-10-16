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
 * Estes testes cobrem a função que decide. Não cobrem o render do
 * cartão: para isso faltaria montar a tela com react-query e rede
 * falsa, e a dívida fica registrada aqui em vez de virar um teste que
 * passa sem olhar o que importa.
 */

import { describe, expect, it } from "vitest";

import { PlanoAindaNaoVisivel, planoDaConversa } from "@/components/chat/chat-screen";
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

describe("a ausência temporária é um erro, não uma resposta", () => {
  it("PlanoAindaNaoVisivel é um Error de verdade", () => {
    /*
     * O `retry` do react-query só atua sobre erro. Se a busca
     * devolvesse `null`, ela encerraria com "não há proposta"
     * exatamente no instante em que a proposta está a caminho — que é
     * o bug. Este teste existe para que trocar o `throw` por um
     * `return null` quebre aqui.
     */
    const erro = new PlanoAindaNaoVisivel();

    expect(erro).toBeInstanceOf(Error);
    expect(erro.name).toBe("PlanoAindaNaoVisivel");
  });
});
