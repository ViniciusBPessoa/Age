/**
 * O aviso de proposta esperando resposta em OUTRA conversa (§5.8).
 *
 * ## O caso que originou isto
 *
 * O usuário pediu "os 4 primeiros filmes da Pixar, um por domingo". O
 * Age propôs os quatro na ordem certa; só três foram aprovados, e o
 * Toy Story de 25/10 ficou pendente.
 *
 * Cinco dias depois ele pediu "os 3 primeiros", o Age olhou a agenda,
 * viu que faltava justamente o primeiro e propôs encaixá-lo no único
 * domingo livre. O usuário não respondeu — mandou outra pergunta em
 * seguida —, e a proposta sumiu da vista.
 *
 * Quando ele foi conferir o calendário, viu a ordem "errada" e não
 * tinha como saber que havia uma correção esperando aprovação. O
 * relato foi "ele por algum motivo não criou como deveria".
 *
 * ## Por que aqui, e não no cartão
 *
 * `plan-review.tsx` já mostra o plano da conversa ABERTA, e isso foi
 * consertado antes (o cartão sobrevive a F5 porque vem de
 * `GET /plans?status=proposed`). O buraco que restava é a proposta de
 * OUTRA conversa: ela existe no banco, continua válida, e não aparece
 * em lugar nenhum — nem na lista de conversas, nem no chat atual.
 *
 * Uma proposta que expira sem ninguém ver é pior que uma recusada: a
 * pessoa acha que o sistema fez o que ela pediu.
 */

"use client";

import Link from "next/link";

import { IconAlert, IconChevronRight } from "@/components/ui";
import type { Plan } from "@/lib/types";

/** As propostas vivas que NÃO são da conversa aberta. */
export function pendentesDeOutrasConversas(
  propostos: Plan[],
  idAtivo: string | null,
): Plan[] {
  return propostos.filter(
    (p) =>
      p.status === "proposed" &&
      !p.is_expired &&
      // Sem conversa de origem não há para onde mandar a pessoa, e o
      // aviso viraria um beco sem saída.
      p.conversation_id !== null &&
      p.conversation_id !== idAtivo,
  );
}

function contarItens(plano: Plan): number {
  return plano.items.filter((i) => i.status === "pending").length;
}

export function PropostasPendentes({
  propostos,
  idAtivo,
}: {
  propostos: Plan[];
  idAtivo: string | null;
}) {
  const pendentes = pendentesDeOutrasConversas(propostos, idAtivo);
  if (pendentes.length === 0) return null;

  return (
    <div
      /*
       * `role="status"` e `aria-live`: o aviso aparece sozinho, sem
       * clique, então quem usa leitor de tela precisa ser avisado.
       * `polite` e não `assertive` — é informação, não emergência.
       */
      role="status"
      aria-live="polite"
      className="mb-3 space-y-2"
    >
      {pendentes.map((plano) => {
        const n = contarItens(plano);
        return (
          <Link
            key={plano.id}
            href={`/chat/${plano.conversation_id}`}
            className="flex items-center gap-2.5 rounded-xl border border-proposed/40 bg-proposed/5 px-3 py-2.5 text-xs transition-colors hover:bg-proposed/10"
          >
            <IconAlert className="h-4 w-4 shrink-0 text-proposed" />
            <span className="min-w-0 flex-1">
              <span className="font-medium text-ink">
                {n === 1
                  ? "1 proposta esperando sua resposta"
                  : `${n} itens esperando sua resposta`}
              </span>
              {plano.summary ? (
                <span className="text-muted"> · {plano.summary}</span>
              ) : null}
            </span>
            <IconChevronRight className="h-3.5 w-3.5 shrink-0 text-muted" />
          </Link>
        );
      })}
    </div>
  );
}
