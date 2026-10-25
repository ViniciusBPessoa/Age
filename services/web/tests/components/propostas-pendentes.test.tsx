/**
 * O aviso de proposta esperando resposta em OUTRA conversa (§5.8).
 *
 * Cada teste traz a sabotagem que precisa derrubá-lo.
 *
 * O caso real: o usuário pediu 4 filmes da Pixar, aprovou 3, e o
 * primeiro ficou pendente. Cinco dias depois o Age notou a falta e
 * propôs o conserto — o usuário não respondeu, mandou outra pergunta,
 * e a proposta sumiu da vista. Quando ele foi conferir o calendário,
 * viu a ordem "errada" sem saber que havia correção esperando.
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  PropostasPendentes,
  pendentesDeOutrasConversas,
} from "@/components/chat/propostas-pendentes";
import type { Plan } from "@/lib/types";

afterEach(cleanup);

function plano(over: Partial<Plan> = {}): Plan {
  return {
    id: "p1",
    conversation_id: "conversa-antiga",
    origin_note: null,
    status: "proposed",
    summary: "Filme: Toy Story (1995)",
    items: [
      {
        id: "i1",
        status: "pending",
      } as unknown as Plan["items"][number],
    ],
    created_at: "2026-10-25T21:00:00Z",
    expires_at: "2026-11-01T21:00:00Z",
    is_expired: false,
    ...over,
  };
}

describe("quais propostas o aviso mostra", () => {
  it("mostra a de OUTRA conversa", () => {
    /*
     * A sabotagem que só este teste pega: filtrar por
     * `conversation_id === idAtivo`, que é o que a tela já fazia.
     *
     * Era exatamente o buraco: a proposta existia no banco, continuava
     * válida, e não aparecia em lugar nenhum.
     */
    const fora = pendentesDeOutrasConversas([plano()], "conversa-aberta");
    expect(fora).toHaveLength(1);
  });

  it("NÃO repete a da conversa aberta", () => {
    /*
     * Sabotagem: não filtrar nada.
     *
     * O cartão de aprovação já mostra essa; avisar de novo no topo
     * seria dizer duas vezes a mesma coisa, na mesma tela.
     */
    const fora = pendentesDeOutrasConversas([plano()], "conversa-antiga");
    expect(fora).toHaveLength(0);
  });

  it("ignora proposta expirada", () => {
    /*
     * Sabotagem: olhar só o `status`.
     *
     * Plano tem TTL. Mandar a pessoa para um cartão que não aplica
     * mais é pior que não avisar.
     */
    const fora = pendentesDeOutrasConversas(
      [plano({ is_expired: true })],
      "outra",
    );
    expect(fora).toHaveLength(0);
  });

  it("ignora plano sem conversa de origem", () => {
    /*
     * Sabotagem: aceitar `conversation_id: null`.
     *
     * O aviso é um link. Sem conversa não há para onde ir, e o clique
     * levaria a lugar nenhum.
     */
    const fora = pendentesDeOutrasConversas(
      [plano({ conversation_id: null })],
      "outra",
    );
    expect(fora).toHaveLength(0);
  });

  it("ignora plano já aplicado ou rejeitado", () => {
    // Sabotagem: não checar `status`. Avisar sobre o que já foi
    // decidido é ruído que treina a pessoa a ignorar o aviso.
    for (const status of ["applied", "rejected"] as const) {
      const fora = pendentesDeOutrasConversas(
        [plano({ status } as Partial<Plan>)],
        "outra",
      );
      expect(fora).toHaveLength(0);
    }
  });
});

describe("o aviso na tela", () => {
  it("diz quantos itens e leva à conversa", () => {
    render(
      <PropostasPendentes propostos={[plano()]} idAtivo="conversa-aberta" />,
    );

    const link = screen.getByRole("link");
    expect(link.getAttribute("href")).toBe("/chat/conversa-antiga");
    expect(link.textContent).toContain("esperando sua resposta");
    // O resumo ajuda a reconhecer o que ficou parado.
    expect(link.textContent).toContain("Toy Story");
  });

  it("é anunciado por leitor de tela", () => {
    /*
     * Sabotagem: remover `role="status"`/`aria-live`.
     *
     * O aviso aparece sozinho, sem clique. Uma pista puramente visual
     * não existe para quem não enxerga — §8.2.
     */
    render(
      <PropostasPendentes propostos={[plano()]} idAtivo="conversa-aberta" />,
    );
    const status = screen.getByRole("status");
    expect(status.getAttribute("aria-live")).toBe("polite");
  });

  it("sem pendência, não desenha nada", () => {
    /*
     * Sabotagem: renderizar a moldura vazia.
     *
     * É o estado normal da esmagadora maioria dos turnos; uma barra
     * vazia no topo de toda conversa seria mobília.
     */
    const { container } = render(
      <PropostasPendentes propostos={[]} idAtivo="x" />,
    );
    expect(container.firstChild).toBeNull();
  });
});
