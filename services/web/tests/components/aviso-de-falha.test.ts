/**
 * Se nao entrou tudo o que foi pedido, a tela diz.
 *
 * `mensagemDeFalha` existe porque a condicao que vivia solta em tres
 * telas estava errada do mesmo jeito nas tres:
 *
 *     if (result.manual_fix_needed.length > 0) setErro(result.message);
 *
 * `manual_fix_needed` e a lista do que a REVERSAO nao conseguiu
 * desfazer. Um `FAILED` nao preenche essa lista, porque num FAILED nao
 * houve o que reverter. Ou seja: o caso mais comum de fracasso, a
 * recusa por conflito bloqueante, era o UNICO que passava calado.
 *
 * Somado ao cartao que se apagava sozinho ao clicar em aprovar, dava o
 * relato do usuario: HTTP 200, nada agendado, nenhuma mensagem, e o
 * cartao sumido. "Tu nunca confirma porra nenhuma."
 */

import { describe, expect, it } from "vitest";

import { mensagemDeFalha } from "@/lib/resultado-do-apply";
import type { ApplyResult } from "@/lib/types";

function resultado(over: Partial<ApplyResult>): ApplyResult {
  return {
    status: "applied",
    applied_count: 0,
    failed: [],
    manual_fix_needed: [],
    message: "",
    ...over,
  };
}

describe("nada entrou", () => {
  it("a recusa por conflito bloqueante fala", () => {
    /*
     * A forma EXATA que o backend devolve ao recusar sem confirmacao:
     * 200, zero aplicados, `manual_fix_needed` vazio. Era o unico caso
     * mudo, e e o caso mais comum.
     *
     * Sabotagem: restaurar `if (manual_fix_needed.length > 0)`. Este
     * teste e o que cai.
     */
    const aviso = mensagemDeFalha(
      resultado({
        status: "failed",
        applied_count: 0,
        failed: [{ item_id: "i1", error: "conflito bloqueante: choca" }],
        manual_fix_needed: [],
        message: "Nada foi aplicado, e a proposta continua esperando.",
      }),
    );

    expect(aviso).toBe("Nada foi aplicado, e a proposta continua esperando.");
  });

  it("sem texto do servidor, ainda avisa", () => {
    // Silencio aqui e o defeito. Aviso generico e melhor que nenhum.
    const aviso = mensagemDeFalha(resultado({ applied_count: 0, message: "" }));

    expect(aviso).not.toBeNull();
    expect((aviso ?? "").length).toBeGreaterThan(5);
  });
});

describe("entrou em parte", () => {
  it("fala quando algum item falhou", () => {
    const aviso = mensagemDeFalha(
      resultado({
        status: "partially_applied",
        applied_count: 2,
        failed: [{ item_id: "i3", error: "a agenda mudou" }],
        message: "2 entraram, 1 falhou.",
      }),
    );

    expect(aviso).toBe("2 entraram, 1 falhou.");
  });

  it("fala quando a reversao deixou pendencia", () => {
    const aviso = mensagemDeFalha(
      resultado({
        applied_count: 1,
        manual_fix_needed: ["apagar o evento X a mao"],
        message: "Precisa de atencao manual.",
      }),
    );

    expect(aviso).toBe("Precisa de atencao manual.");
  });
});

describe("entrou tudo", () => {
  it("nao inventa aviso no caminho feliz", () => {
    /*
     * Contrapeso indispensavel: "avisar sempre" passaria em todos os
     * testes acima e encheria a tela de alarme falso a cada aprovacao
     * bem-sucedida.
     */
    const aviso = mensagemDeFalha(
      resultado({ applied_count: 3, message: "Pronto. 3 mudancas." }),
    );

    expect(aviso).toBeNull();
  });
});
