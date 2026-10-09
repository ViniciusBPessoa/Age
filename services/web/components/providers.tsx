/**
 * TanStack Query, e não Redux (§2.4).
 *
 * "Praticamente todo o estado desta UI é cache de servidor: eventos,
 * conversas, planos, preferências." Redux seria reimplementar cache e
 * revalidação à mão. O estado genuinamente local — rascunho da
 * mensagem, itens selecionados no diff — fica em `useState`.
 */

"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { RequestFailed } from "@/lib/api-client";

export function Providers({ children }: { children: ReactNode }) {
  const router = useRouter();

  // `useState` e não módulo: um QueryClient em escopo de módulo seria
  // compartilhado entre requisições no servidor, e com ele o cache de
  // um usuário vazaria para outro.
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // 30 s: a agenda muda por fora (§5.10), então cache longo
            // mostraria um estado que não existe mais. Mas zero faria
            // toda navegação refetchar, e a maior parte das telas é
            // consultada em sequência.
            staleTime: 30_000,
            refetchOnWindowFocus: true,
            retry: (count, error) => {
              // 401 já foi tratado pelo BFF, que tentou renovar uma vez
              // (§4.1.2). Se chegou aqui, a sessão acabou — repetir
              // só atrasaria o redirect.
              if (error instanceof RequestFailed && error.isAuth) return false;
              if (
                error instanceof RequestFailed &&
                error.status >= 400 &&
                error.status < 500
              ) {
                return false;
              }
              return count < 2;
            },
          },
          mutations: { retry: false },
        },
      }),
  );

  // Sessão expirada em qualquer query manda ao login. Fica aqui, uma
  // vez, em vez de em cada tela.
  //
  // Dentro de `useEffect` com cleanup, e não no corpo do componente:
  // `subscribe` devolve a função de cancelar, e chamá-lo direto no
  // render criaria uma assinatura nova a cada renderização, todas
  // vivas. Com React 19 em StrictMode o efeito monta duas vezes, o que
  // torna o vazamento visível rápido — mas sem o cleanup ele existiria
  // em produção do mesmo jeito, só mais devagar.
  useEffect(() => {
    return client.getQueryCache().subscribe((event) => {
      const error = event.query.state.error;
      if (error instanceof RequestFailed && error.isAuth) {
        router.replace("/login");
      }
    });
  }, [client, router]);

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
