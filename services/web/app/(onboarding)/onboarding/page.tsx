/**
 * Onboarding — "a tela mais importante do produto, e a que define se
 * alguém chega a usá-lo" (§8.1).
 *
 * O problema que ela resolve: o Age exige chave de API de LLM (§4.3) e,
 * sem ela, o chat não responde. Jogar a pessoa num chat morto é a falha
 * de produto mais provável aqui.
 *
 * O passo é lido de `users.onboarding_step` no servidor, então recarregar
 * a página ou voltar amanhã retoma de onde parou — em vez de recomeçar,
 * que é como se perde alguém que já tinha feito metade.
 */

import { redirect } from "next/navigation";

import { OnboardingFlow } from "@/components/onboarding-flow";
import { Unauthenticated, apiGet, apiGetOptional } from "@/lib/api-server";
import type { Credential, Me, ProviderInfo, TaskListRef } from "@/lib/types";
import type { CalendarRef } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function OnboardingPage() {
  let me: Me;
  try {
    me = await apiGet<Me>("/auth/me");
  } catch (err) {
    if (err instanceof Unauthenticated) redirect("/login");
    throw err;
  }

  // Já terminou? Não há por que ver isto de novo.
  if (me.onboarding_step >= 3) redirect("/chat");

  // `Optional` nos três: se o catálogo do worker estiver fora do ar, o
  // onboarding ainda deve abrir e dizer o que aconteceu — melhor que
  // uma tela de erro na primeira coisa que a pessoa vê (§5.9.13).
  const [providers, credentials, calendars, lists] = await Promise.all([
    apiGetOptional<ProviderInfo[]>("/llm/providers"),
    apiGetOptional<Credential[]>("/llm/credentials"),
    apiGetOptional<CalendarRef[]>("/calendar/calendars"),
    apiGetOptional<TaskListRef[]>("/tasks/lists"),
  ]);

  return (
    <OnboardingFlow
      me={me}
      providers={providers}
      initialCredentials={credentials ?? []}
      calendars={calendars ?? []}
      taskLists={lists ?? []}
    />
  );
}
