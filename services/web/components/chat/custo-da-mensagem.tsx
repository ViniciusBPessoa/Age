/**
 * Quanto custou aquela resposta.
 *
 * Linha discreta sob a mensagem, no padrão do `ToolTrail`: metadado,
 * não cartão. Só em `role === "assistant"`, porque o custo é do PAR
 * (pergunta + resposta) e é na resposta que a cobrança acontece — o
 * provedor cobra o prompt inteiro, que já inclui a pergunta.
 *
 * O número vem de duas vias, e as duas são necessárias: o `UsageEvent`
 * do SSE o entrega ao vivo, e `GET /messages` o traz de volta depois de
 * um F5. Sem a segunda, recarregar a página apagaria o custo.
 */

"use client";

import { formatTokens, formatUsd, useMostrarCusto } from "@/lib/usar-custo";
import type { Message } from "@/lib/types";

export function CustoDaMensagem({ message }: { message: Message }) {
  const mostrarCusto = useMostrarCusto();

  const tokens = (message.prompt_tokens ?? 0) + (message.completion_tokens ?? 0);

  // Nada a dizer: mensagem do usuário, histórico anterior à migration,
  // ou turno que falhou antes de gerar.
  if (!tokens) return null;

  const custo = message.estimated_cost_usd;

  return (
    <p className="mt-1.5 flex items-center gap-1.5 px-1 text-2xs text-faint">
      <span className="tnum">{formatTokens(tokens)} tokens</span>
      {mostrarCusto && custo !== null ? (
        <>
          <span aria-hidden="true">·</span>
          {/* `title` com o valor cheio: o rodapé arredonda, e quem quer
              auditar precisa do número que o banco guarda. */}
          <span className="tnum" title={`${custo} USD`}>
            {formatUsd(custo)}
          </span>
        </>
      ) : null}
    </p>
  );
}
