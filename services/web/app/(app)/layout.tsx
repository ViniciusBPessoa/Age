/**
 * Shell do app: navegação + área de conteúdo.
 *
 * Server Component (§2.5: "layout.tsx — shell: nav + timeline
 * (Server)"). Ele resolve a sessão uma vez, no servidor, e manda ao
 * login se não houver — em vez de cada tela renderizar vazia e
 * redirecionar no cliente, que é o que produz o flash de tela errada.
 */

import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { AppNav } from "@/components/app-nav";
import { Unauthenticated, apiGet, hasSessionCookie } from "@/lib/api-server";
import type { Me } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function AppLayout({ children }: { children: ReactNode }) {
  if (!(await hasSessionCookie())) redirect("/login");

  let me: Me;
  try {
    me = await apiGet<Me>("/auth/me");
  } catch (err) {
    if (err instanceof Unauthenticated) redirect("/login");
    throw err;
  }

  // Quem não terminou o onboarding não tem credencial de LLM, e o chat
  // não responderia. Mandar para lá é melhor que um chat morto (§8.1).
  if (me.onboarding_step < 3) redirect("/onboarding");

  return (
    <div className="flex min-h-dvh flex-col md:flex-row">
      <AppNav me={me} />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}
