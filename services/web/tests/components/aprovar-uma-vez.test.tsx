/**
 * Aprovar só pode acontecer uma vez (§5.2).
 *
 * O botão de aprovar continuava clicável enquanto a aplicação estava
 * em curso: `canApply` olhava a seleção, a confirmação de conflito e a
 * validade do plano, mas não `applying`. O de rejeitar, a poucos
 * pixels de distância, já conferia.
 *
 * Dois cliques mandavam dois `apply` do mesmo plano, e cada `apply`
 * escreve na agenda. O sintoma seria o evento duplicado — o mesmo que
 * o usuário já relatou por outra causa, e o tipo de coisa que ele
 * descobriria olhando a agenda depois, não na hora.
 *
 * A assimetria passou despercebida porque a tela PARECE ocupada: o
 * texto do botão muda para "Aprovando…", o que convence quem revisa o
 * código e não impede clique nenhum.
 *
 * Nos logs do servidor eu vi o par `204` e depois `404` para o mesmo
 * item, que é a assinatura desse tipo de clique duplo chegando ao
 * backend.
 */

import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PlanReview } from "@/components/plan-review/plan-review";
import type { Plan, PlanItem } from "@/lib/types";

const TZ = "America/Sao_Paulo";

function item(over: Partial<PlanItem> = {}): PlanItem {
  return {
    id: "item-1",
    target: "event",
    op: "CREATE",
    before: null,
    after: {
      title: "Classes em Python",
      start: "2026-10-19T16:00:00-03:00",
      end: "2026-10-19T17:00:00-03:00",
    },
    reason: "primeiro bloco",
    conflicts: [],
    links: [],
    status: "pending",
    is_blocking: false,
    ...over,
  };
}

function plano(items: PlanItem[]): Plan {
  return {
    id: "plano-1",
    conversation_id: "conversa-1",
    status: "proposed",
    summary: "agenda: 1 para criar",
    items,
    created_at: "2026-10-16T05:00:00Z",
    expires_at: "2026-10-17T05:00:00Z",
    origin_note: null,
    is_expired: false,
  };
}

function montar(applying: boolean) {
  const onApply = vi.fn();
  render(
    <PlanReview
      plan={plano([item()])}
      timezone={TZ}
      applying={applying}
      onApply={onApply}
      onReject={vi.fn()}
    />,
  );
  return onApply;
}

function botaoAprovar(): HTMLButtonElement {
  /*
   * O rótulo vem de `applyLabel`, que diz "Aplicar o item" ou
   * "Aplicar os N itens" — não "Aprovar". Escrevi o seletor pelo que
   * eu ACHAVA que o botão dizia e os três testes falharam por não
   * achar elemento nenhum, que é o jeito barato de descobrir isso.
   *
   * `/^Aplicar/` cobre as quatro variações do rótulo sem amarrar o
   * teste à contagem.
   */
  const encontrado = screen
    .getAllByRole("button")
    .find((b) => /^Aplicar/.test(b.textContent ?? ""));
  if (!encontrado) throw new Error("botão de aplicar não encontrado");
  return encontrado as HTMLButtonElement;
}

describe("o botão de aprovar durante a aplicação", () => {
  it("fica desabilitado enquanto aplica", () => {
    /*
     * O teste que falha se `!applying` sair de `canApply`. É a defesa
     * real: `disabled` é o que o navegador respeita, independente de
     * quantas vezes a pessoa clique.
     */
    montar(true);

    expect(botaoAprovar().disabled).toBe(true);
  });

  it("não chama onApply num segundo clique", async () => {
    // Comportamento, não só atributo: um `disabled` que o componente
    // ignorasse por ter handler em outro elemento passaria no teste
    // acima e falharia aqui.
    const onApply = montar(true);

    await act(async () => {
      fireEvent.click(botaoAprovar());
      fireEvent.click(botaoAprovar());
    });

    expect(onApply).not.toHaveBeenCalled();
  });

  it("continua clicável quando não está aplicando", () => {
    /*
     * O contrapeso, e ele importa: desabilitar sempre também passaria
     * nos dois testes acima e deixaria ninguém aprovar nada.
     */
    const onApply = montar(false);

    expect(botaoAprovar().disabled).toBe(false);

    fireEvent.click(botaoAprovar());
    expect(onApply).toHaveBeenCalledTimes(1);
  });
});
