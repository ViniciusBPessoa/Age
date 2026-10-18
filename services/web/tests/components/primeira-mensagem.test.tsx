/**
 * A primeira mensagem responde igual a todas as outras.
 *
 * Relato: "toda vez que um usuario inicia um chat novo, ele nao traz
 * aquela mensagem de feedback na parte de baixo, dizendo 'leu mensagem,
 * aguardando resposta'. Nao tem. So a partir da segunda mensagem que
 * isso acontece hoje."
 *
 * Era literal, e a causa estava na ORDEM dentro de `send`:
 *
 *   1. cria a conversa          <- await, so na primeira mensagem
 *   2. invalida a lista         <- await, so na primeira mensagem
 *   3. setStreaming(true)       <- o indicador so comeca AQUI
 *   4. manda a mensagem
 *
 * Da segunda em diante os passos 1 e 2 sao pulados, e o indicador
 * aparecia no mesmo tique do clique. Na primeira, ele esperava duas
 * idas a rede inteiras, e durante elas a tela seguia mostrando os
 * quatro exemplos do estado vazio, como se nada tivesse acontecido.
 *
 * Havia um segundo sumico no mesmo instante, que ninguem tinha
 * nomeado: nao existia eco da mensagem enviada. A lista vem do banco e
 * so e recarregada quando o turno termina, entao o texto que a pessoa
 * acabou de digitar desaparecia da tela ate a resposta chegar.
 */

import { describe, expect, it } from "vitest";

import { comEcoLocal, mostraEstadoVazio } from "@/components/chat/chat-screen";
import type { Message } from "@/lib/types";

const CONVERSA = "aaaaaaaa-1111-2222-3333-444444444444";

function msg(content: string, role: Message["role"] = "user"): Message {
  return {
    id: "m-" + content.slice(0, 8),
    role,
    content,
    created_at: new Date().toISOString(),
  } as Message;
}

describe("o indicador aparece assim que o turno comeca", () => {
  it("conversa nova, turno em andamento: nao mostra o estado vazio", () => {
    /*
     * Esta e a combinacao exata do defeito: ainda nao ha id de
     * conversa (ela esta sendo criada), ainda nao ha mensagem na
     * lista, e o turno ja comecou. Antes a tela caia no estado vazio,
     * e o indicador vive dentro do `MessageList`, que nem era montado.
     */
    expect(mostraEstadoVazio(null, 0, true, false, false)).toBe(false);
  });

  it("conversa nova, antes de enviar: mostra o estado vazio", () => {
    // Contrapeso: "nunca mostrar" passaria no teste acima e deixaria a
    // tela de boas-vindas inacessivel.
    expect(mostraEstadoVazio(null, 0, false, false, false)).toBe(true);
  });

  it("ja com resposta chegando, nao mostra o estado vazio", () => {
    expect(mostraEstadoVazio(null, 0, false, true, false)).toBe(false);
  });
});

describe("a mensagem enviada aparece na hora", () => {
  it("entra no fim da lista enquanto o servidor nao devolve", () => {
    const lista = comEcoLocal([], "organiza meus estudos");

    expect(lista.length).toBe(1);
    expect(lista[0].content).toBe("organiza meus estudos");
    expect(lista[0].role).toBe("user");
  });

  it("preserva o que ja veio do servidor", () => {
    const lista = comEcoLocal([msg("oi"), msg("ola", "assistant")], "e agora?");

    expect(lista.map((m) => m.content)).toEqual(["oi", "ola", "e agora?"]);
  });

  it("sem eco, devolve a lista intacta", () => {
    const doServidor = [msg("oi")];
    expect(comEcoLocal(doServidor, null)).toBe(doServidor);
  });

  it("nao duplica quando a mensagem ja chegou do servidor", () => {
    /*
     * O eco e limpo no `done`, mas a lista pode ser recarregada antes
     * disso. Sem esta guarda, a frase piscaria duas vezes na tela.
     */
    const lista = comEcoLocal([msg("organiza meus estudos")], "organiza meus estudos");

    expect(lista.length).toBe(1);
  });

  it("nao confunde com uma resposta de mesmo texto", () => {
    // Contrapeso da guarda acima: so mensagem DO USUARIO conta como
    // "ja chegou". Uma resposta que repetisse o texto nao pode fazer o
    // eco sumir antes da hora.
    const lista = comEcoLocal([msg("confirma", "assistant")], "confirma");

    expect(lista.length).toBe(2);
  });
});
