/**
 * O primeiro turno de uma conversa nova (§8.1).
 *
 * Relato: "o feedback só aparece a partir da segunda mensagem; na
 * primeira não aparece nada, e só consigo ver a resposta se der F5".
 *
 * Os dois sintomas tinham UMA causa. Assim que a conversa nascia, o
 * código chamava `router.replace('/chat/<id>')` no meio do turno.
 * `/chat` e `/chat/[id]` são páginas diferentes, então a troca remonta
 * o ChatScreen: o `streaming` volta a `false`, o texto acumulado se
 * perde, e o efeito de limpeza fecha o EventSource. O turno continuava
 * no servidor e terminava bem, e era por isso que o F5 mostrava a
 * resposta — a tela que a acompanhava é que já não existia.
 *
 * A partir da segunda mensagem a conversa já tinha id na URL, nenhuma
 * navegação acontecia, e tudo funcionava. Daí o "só na primeira".
 *
 * O teste é de sequência, não de render: o caso perigoso é o do meio
 * do turno, e ele só aparece olhando os três momentos em ordem.
 */

import { describe, expect, it } from "vitest";

import { podeNavegarParaNova } from "@/components/chat/chat-screen";

const NOVA = "11111111-2222-3333-4444-555555555555";

describe("quando a URL pode mudar para a conversa nova", () => {
  it("não navega enquanto o turno está em curso", () => {
    // O caso do relato. Este é o único que precisava mudar, e é o que
    // quebrava as duas coisas de uma vez.
    expect(podeNavegarParaNova(true, NOVA, null)).toBe(false);
  });

  it("navega quando o turno termina", () => {
    // Aqui é seguro: não há stream aberto nem texto parcial a perder,
    // e a mensagem já foi recarregada do banco no `done`.
    expect(podeNavegarParaNova(false, NOVA, null)).toBe(true);
  });

  it("não navega quando a URL já tem o id", () => {
    // Segunda mensagem em diante. Navegar seria um no-op que ainda
    // assim remonta a árvore e perderia o turno de novo.
    expect(podeNavegarParaNova(false, NOVA, NOVA)).toBe(false);
  });

  it("não navega quando nenhuma conversa foi criada nesta tela", () => {
    // Conversa aberta pela sidebar: não há nada a corrigir na URL.
    expect(podeNavegarParaNova(false, null, NOVA)).toBe(false);
    expect(podeNavegarParaNova(false, null, null)).toBe(false);
  });

  it("a sequência inteira do primeiro turno", () => {
    // Os três momentos em ordem, que é como o defeito aparecia:
    // antes de enviar, durante o turno, e depois do `done`.
    const antes = podeNavegarParaNova(false, null, null);
    const durante = podeNavegarParaNova(true, NOVA, null);
    const depois = podeNavegarParaNova(false, NOVA, null);

    expect([antes, durante, depois]).toEqual([false, false, true]);
  });

  it("streaming vence mesmo com a conversa criada", () => {
    // Guarda explícita contra a tentação de simplificar para
    // "tem id? navega": era exatamente essa a regra antiga.
    expect(podeNavegarParaNova(true, NOVA, null)).toBe(false);
    expect(podeNavegarParaNova(true, NOVA, NOVA)).toBe(false);
  });
});
