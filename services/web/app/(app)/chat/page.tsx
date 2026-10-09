/**
 * Chat sem conversa escolhida: o estado vazio (§8.1).
 *
 * A conversa só é criada quando a primeira mensagem é enviada, e não ao
 * abrir a tela. A diferença aparece na sidebar: criar na abertura
 * encheria o histórico de conversas vazias de toda vez que alguém
 * clicou em "nova" e mudou de ideia.
 */

import { ChatScreen } from "@/components/chat/chat-screen";
import { Unauthenticated, apiGet, apiGetOptional } from "@/lib/api-server";
import type {
  CalendarRef,
  Credential,
  Me,
  Preferences,
  ProviderInfo,
} from "@/lib/types";
import { redirect } from "next/navigation";

export const dynamic = "force-dynamic";

export default async function ChatPage() {
  let me: Me;
  try {
    me = await apiGet<Me>("/auth/me");
  } catch (err) {
    if (err instanceof Unauthenticated) redirect("/login");
    throw err;
  }

  const [providers, credentials, calendars, preferences] = await Promise.all([
    apiGetOptional<ProviderInfo[]>("/llm/providers"),
    apiGetOptional<Credential[]>("/llm/credentials"),
    apiGetOptional<CalendarRef[]>("/calendar/calendars"),
    apiGetOptional<Preferences>("/preferences"),
  ]);

  return (
    <ChatScreen
      me={me}
      conversationId={null}
      providers={providers ?? []}
      credentials={credentials ?? []}
      calendars={calendars ?? []}
      accountTarget={preferences?.default_target ?? "ask"}
    />
  );
}
