/**
 * O que a tela deve dizer depois de um `POST /plans/{id}/apply`.
 *
 * Existe como função nomeada, num arquivo próprio, porque a condição
 * que vivia solta em três telas estava errada do mesmo jeito nas três:
 *
 *     if (result.manual_fix_needed.length > 0) setErro(result.message);
 *
 * `manual_fix_needed` é a lista do que a REVERSÃO não conseguiu
 * desfazer. Um `FAILED` não preenche essa lista, porque num FAILED não
 * houve o que reverter. Ou seja: o caso mais comum de fracasso, a
 * recusa por conflito bloqueante, era o único que passava calado.
 *
 * O resultado, somado ao cartão que se apagava sozinho ao clicar em
 * aprovar, era o relato do usuário: HTTP 200, nada agendado, nenhuma
 * mensagem, e o cartão sumido. "Tu nunca confirma porra nenhuma."
 *
 * A regra agora é a óbvia, e cabe numa frase: se não entrou tudo o que
 * foi pedido, a tela diz.
 */

import type { ApplyResult } from "./types";

export function mensagemDeFalha(result: ApplyResult): string | null {
  /*
   * Nada entrou. Sempre avisa, mesmo que o servidor não tenha mandado
   * texto: silêncio aqui é o defeito que estamos consertando, e um
   * aviso genérico é melhor que nenhum.
   */
  if (result.applied_count === 0) {
    return result.message || "Nada foi aplicado.";
  }

  // Entrou em parte: o que falhou precisa aparecer.
  if (result.manual_fix_needed.length > 0 || result.failed.length > 0) {
    return result.message || "Parte do plano não entrou.";
  }

  return null;
}
