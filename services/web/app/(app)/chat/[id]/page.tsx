/**
 * Uma conversa. Mesma tela do chat vazio, com o id resolvido.
 *
 * Os dados do rodapé (provedores, credenciais, agendas, preferência de
 * destino) são buscados no servidor e passados como props, em vez de o
 * cliente buscar quatro coisas ao montar. A diferença é visível: no
 * segundo caminho o seletor de provedor aparece vazio por um instante e
 * depois preenche, o que dá a impressão de que a configuração foi
 * perdida.
 */

import { redirect } from "next/navigation";

import { ChatScreen } from "@/components/chat/chat-screen";
import { Unauthenticated, apiGet, apiGetOptional } from "@/lib/api-server";
import type {
  CalendarRef,
  Credential,
  KeySource,
  Me,
  Preferences,
  ProviderInfo,
} from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function ConversationPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  let me: Me;
  try {
    me = await apiGet<Me>("/auth/me");
  } catch (err) {
    if (err instanceof Unauthenticated) redirect("/login");
    throw err;
  }

  const [providers, credentials, calendars, preferences, keySource] = await Promise.all(
    [
      apiGetOptional<ProviderInfo[]>("/llm/providers"),
      apiGetOptional<Credential[]>("/llm/credentials"),
      apiGetOptional<CalendarRef[]>("/calendar/calendars"),
      apiGetOptional<Preferences>("/preferences"),
      apiGetOptional<KeySource>("/llm/key-source"),
    ],
  );

  return (
    <ChatScreen
      me={me}
      conversationId={id}
      providers={providers ?? []}
      credentials={credentials ?? []}
      keySource={keySource ?? null}
      calendars={calendars ?? []}
      accountTarget={preferences?.default_target ?? "ask"}
    />
  );
}
