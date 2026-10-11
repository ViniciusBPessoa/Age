/**
 * A tela principal (§8.1): conversa à esquerda, sidebar, e o plano
 * aparecendo inline quando é proposto.
 *
 * O ciclo de um turno, do ponto de vista desta tela:
 *
 *   1. POST /conversations/{id}/messages  -> 202 {turn_id, stream_url}
 *   2. abre o SSE em /turns/{id}/stream
 *   3. tool_start/tool_end  -> linhas de progresso (§8.1)
 *   4. token                -> texto crescendo
 *   5. plan                 -> busca o plano e mostra o diff
 *   6. done                 -> recarrega a conversa e encerra
 *
 * O passo 5 merece nota: o evento `plan` traz só `plan_id` e
 * `item_count`. O diff completo vem por `GET /plans/{id}`, e não pelo
 * stream, porque ele é grande e porque o endereço do plano é o que
 * permite recarregar a página sem perder a proposta.
 */

"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  ChatEmptyState,
  MessageList,
  type ToolTrace,
} from "@/components/chat/messages";
import { Composer } from "@/components/chat/composer";
import { ConversationSidebar } from "@/components/conversation-list/sidebar";
import { PlanReview } from "@/components/plan-review/plan-review";
import { Button, IconAlert, IconInfo, IconX, LoadError, cx } from "@/components/ui";
import { RequestFailed, api } from "@/lib/api-client";
import { subscribeToTurn, toolLabel, type StreamEvent } from "@/lib/sse";
import type {
  ApplyResult,
  CalendarRef,
  Conversation,
  Credential,
  ExternalChanges,
  Me,
  Message,
  Plan,
  ProviderInfo,
  Target,
} from "@/lib/types";

interface ConversationPage {
  items: Conversation[];
  next_cursor: string | null;
}

export function ChatScreen({
  me,
  conversationId,
  providers,
  credentials,
  calendars,
  accountTarget,
}: {
  me: Me;
  conversationId: string | null;
  providers: ProviderInfo[];
  credentials: Credential[];
  calendars: CalendarRef[];
  /** Camada 1 de §5.7.2: a preferência da conta. */
  accountTarget: Target;
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const timezone = me.timezone;

  const [streaming, setStreaming] = useState(false);
  const [streamingText, setStreamingText] = useState("");
  const [traces, setTraces] = useState<ToolTrace[]>([]);
  const [planId, setPlanId] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  const [turnError, setTurnError] = useState<string | null>(null);
  const unsubscribe = useRef<(() => void) | null>(null);

  // Encerra o stream ao sair da tela. Sem isto, trocar de conversa
  // deixaria o EventSource anterior vivo, e os tokens do turno antigo
  // continuariam chegando na conversa nova.
  useEffect(() => () => unsubscribe.current?.(), []);

  const conversations = useQuery({
    queryKey: ["conversations"],
    queryFn: () => api.get<ConversationPage>("/conversations", { limit: 50 }),
    // O título é gerado por um job assíncrono com `_defer_by=3`
    // (§6.2.2): no `done` do turno ele ainda não existe, e a
    // invalidação feita ali traz a conversa sem ele — depois nada mais
    // busca, e a sidebar fica "Sem título" até trocar de tela.
    //
    // A condição vive na query, e não num `setTimeout` após o `done`,
    // porque aqui ela vale para qualquer conversa sem título, não só
    // para a que acabou de ser criada: enquanto houver alguma, a lista
    // volta a buscar; quando todas têm, o polling para sozinho.
    refetchInterval: (query) =>
      query.state.data?.items.some((c) => c.title === null) ? 4000 : false,
    // Sem isto o polling pausa quando a janela perde o foco, e é
    // justamente aí que o título costuma chegar: a pessoa manda a
    // mensagem e troca de aba enquanto espera.
    refetchIntervalInBackground: true,
  });

  const messages = useQuery({
    queryKey: ["messages", conversationId],
    queryFn: () =>
      api.get<{ items: Message[] }>(`/conversations/${conversationId}/messages`, {
        limit: 100,
      }),
    enabled: conversationId !== null,
  });

  const active = conversations.data?.items.find((c) => c.id === conversationId);

  /**
   * §5.10: reativo, nunca notificação.
   *
   * Chamado ao abrir o chat, com `acknowledge=true`. A mudança externa
   * é reconhecida **uma vez** — sem isso a mesma reunião voltaria em
   * toda abertura, e o produto deixaria de ser reativo para passar a
   * perseguir o usuário.
   */
  const changes = useQuery({
    queryKey: ["external-changes"],
    queryFn: () =>
      api.get<ExternalChanges>("/calendar/external-changes", {
        acknowledge: true,
      }),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });

  const plan = useQuery({
    queryKey: ["plan", planId],
    queryFn: () => api.get<Plan>(`/plans/${planId}`),
    enabled: planId !== null,
  });

  const handleEvent = useCallback(
    (event: StreamEvent) => {
      switch (event.type) {
        case "tool_start":
          setTraces((previous) => [
            ...previous,
            { name: event.name, label: toolLabel(event.name), done: false },
          ]);
          break;
        case "tool_end":
          setTraces((previous) => {
            const next = [...previous];
            // De trás para frente: a mesma tool pode rodar duas vezes
            // num turno, e marcar a primeira ocorrência deixaria a
            // segunda girando para sempre.
            for (let i = next.length - 1; i >= 0; i -= 1) {
              if (next[i].name === event.name && !next[i].done) {
                next[i] = {
                  ...next[i],
                  done: true,
                  ok: event.ok,
                  summary: event.summary,
                };
                break;
              }
            }
            return next;
          });
          break;
        case "token":
          setStreamingText((previous) => previous + event.delta);
          break;
        case "plan":
          setPlanId(event.plan_id);
          break;
        case "done":
          setStreaming(false);
          // Recarrega do banco: a mensagem persistida é a fonte, e o
          // texto acumulado no cliente era só a pré-visualização.
          void queryClient.invalidateQueries({ queryKey: ["messages"] });
          void queryClient.invalidateQueries({ queryKey: ["conversations"] });
          setStreamingText("");
          break;
        case "error":
          setStreaming(false);
          setTurnError(event.message);
          break;
      }
    },
    [queryClient],
  );

  async function send(text: string) {
    setTurnError(null);
    setTraces([]);
    setStreamingText("");
    setPlanId(null);

    let targetId = conversationId;
    try {
      // Primeira mensagem sem conversa: cria uma. O título vem depois,
      // automático (§6.2.2).
      if (!targetId) {
        const created = await api.post<Conversation>("/conversations", {});
        targetId = created.id;
        await queryClient.invalidateQueries({ queryKey: ["conversations"] });
        router.replace(`/chat/${created.id}`);
      }

      setStreaming(true);
      const accepted = await api.post<{ turn_id: string }>(
        `/conversations/${targetId}/messages`,
        { content: text, client_turn_id: crypto.randomUUID() },
      );

      await queryClient.invalidateQueries({ queryKey: ["messages", targetId] });

      unsubscribe.current?.();
      unsubscribe.current = subscribeToTurn(accepted.turn_id, {
        onEvent: handleEvent,
        onClose: () => setStreaming(false),
        onError: () => {
          setStreaming(false);
          setTurnError(
            "Perdi a conexão com a resposta. O turno pode ter continuado — recarregue para ver.",
          );
        },
      });
    } catch (err) {
      setStreaming(false);
      setTurnError(
        err instanceof RequestFailed ? err.message : "Não consegui enviar a mensagem.",
      );
    }
  }

  async function applyPlan(itemIds: string[], confirmBlocking: boolean) {
    if (!planId) return;
    setApplying(true);
    try {
      const result = await api.post<ApplyResult>(`/plans/${planId}/apply`, {
        item_ids: itemIds,
        confirm_blocking: confirmBlocking,
      });
      setPlanId(null);
      // A agenda e a lista mudaram: invalidar tudo que as mostra.
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["events"] }),
        queryClient.invalidateQueries({ queryKey: ["tasks"] }),
        queryClient.invalidateQueries({ queryKey: ["conversations"] }),
      ]);
      if (result.manual_fix_needed.length > 0) {
        setTurnError(result.message);
      }
    } catch (err) {
      setTurnError(
        err instanceof RequestFailed ? err.message : "Não consegui aplicar.",
      );
    } finally {
      setApplying(false);
    }
  }

  async function mutateConversation(run: () => Promise<unknown>) {
    await run().catch(() => undefined);
    await queryClient.invalidateQueries({ queryKey: ["conversations"] });
  }

  const primaryCalendar =
    calendars.find((c) => c.is_primary)?.name ??
    calendars[0]?.name ??
    "Calendário do Age";

  // §5.7.2, camadas 1 e 2: a conversa sobrepõe a conta; `null` na
  // conversa significa herdar, e é por isso que a coluna é nullable.
  const effectiveTarget: Target = active?.target_default ?? accountTarget;

  return (
    <div className="flex h-dvh">
      {/* `md:` dá 768px, onde a nav principal (240px) e esta lista
          (256px) deixam pouco para a conversa. Subir para `lg:`
          pioraria outra coisa: não há gaveta de conversas abaixo
          deste ponto, então a lista some e não há como trocar de
          conversa. A estreiteza em 768px é o mal menor até existir
          essa gaveta — anotado em PROGRESSO.md. */}
      <div className="hidden w-64 shrink-0 md:block">
        <ConversationSidebar
          conversations={conversations.data?.items ?? []}
          counts={Object.fromEntries(
            (conversations.data?.items ?? []).map((c) => [
              c.id,
              { events: c.applied_events, tasks: c.applied_tasks },
            ]),
          )}
          activeId={conversationId}
          timezone={timezone}
          loading={conversations.isLoading}
          onNew={() => router.push("/chat")}
          onSearch={(term) => {
            void queryClient.fetchQuery({
              queryKey: ["conversations"],
              queryFn: () =>
                api.get<ConversationPage>("/conversations", {
                  limit: 50,
                  q: term || undefined,
                }),
            });
          }}
          onRename={(id, title) =>
            mutateConversation(() => api.patch(`/conversations/${id}`, { title }))
          }
          onPin={(id, pinned) =>
            mutateConversation(() =>
              api.post(`/conversations/${id}/${pinned ? "pin" : "unpin"}`),
            )
          }
          onArchive={(id) =>
            mutateConversation(() => api.post(`/conversations/${id}/archive`))
          }
          onDelete={async (id) => {
            await mutateConversation(() => api.del(`/conversations/${id}`));
            if (id === conversationId) router.replace("/chat");
          }}
          onFork={async (id) => {
            try {
              const forked = await api.post<Conversation>(
                `/conversations/${id}/fork`,
                {},
              );
              await queryClient.invalidateQueries({ queryKey: ["conversations"] });
              router.push(`/chat/${forked.id}`);
            } catch {
              // Silencioso de propósito: ramificar é ação secundária, e
              // um alerta modal por falha nela interromperia o fluxo
              // principal sem o usuário ter perdido nada.
            }
          }}
        />
      </div>

      <main className="flex min-w-0 flex-1 flex-col pb-14 md:pb-0">
        {/* §5.10: o reconhecimento aparece uma vez, no topo, e não como
            notificação. */}
        {changes.data && changes.data.changes.length > 0 ? (
          <div
            role="status"
            className={cx(
              "flex items-start gap-2.5 border-b px-4 py-3 text-sm leading-relaxed md:px-6",
              changes.data.conflict_count > 0
                ? "border-warn/25 bg-warn/8 text-warn"
                : "border-line bg-surface-2 text-ink-2",
            )}
          >
            {changes.data.conflict_count > 0 ? (
              <IconAlert className="mt-0.5 h-4 w-4 shrink-0" />
            ) : (
              <IconInfo className="mt-0.5 h-4 w-4 shrink-0 text-muted" />
            )}
            <span className="min-w-0">{changes.data.summary_text}</span>
          </div>
        ) : null}

        {/*
         * O estado vazio sai de cena assim que o turno comeca.
         *
         * A condicao so olhava a lista de mensagens, que so e
         * recarregada quando o turno termina — entao quem mandava a
         * primeira mensagem via os quatro exemplos continuarem ali
         * durante os segundos de espera, como se o envio nao tivesse
         * acontecido.
         */}
        {(conversationId === null || (messages.data?.items.length ?? 0) === 0) &&
        !streaming &&
        !streamingText ? (
          <ChatEmptyState
            calendarName={primaryCalendar}
            onPick={(text) => void send(text)}
          />
        ) : messages.isError ? (
          <LoadError
            message="Não consegui carregar esta conversa."
            onRetry={() => void messages.refetch()}
          />
        ) : (
          <MessageList
            messages={messages.data?.items ?? []}
            streaming={streaming}
            streamingText={streamingText}
            traces={traces}
            timezone={timezone}
          >
            {plan.data && plan.data.status === "proposed" ? (
              <PlanReview
                plan={plan.data}
                timezone={timezone}
                applying={applying}
                onApply={applyPlan}
                onReject={async () => {
                  await api.post(`/plans/${planId}/reject`).catch(() => undefined);
                  setPlanId(null);
                }}
              />
            ) : null}
          </MessageList>
        )}

        {turnError ? (
          <div className="px-4 md:px-6">
            <div
              role="alert"
              className="mx-auto mb-2 flex max-w-3xl items-start gap-2.5 rounded-xl bg-danger/10 px-3.5 py-2.5 text-sm ring-1 ring-inset ring-danger/25"
            >
              <IconAlert className="mt-0.5 h-4 w-4 shrink-0 text-danger" />
              <span className="min-w-0 flex-1 leading-relaxed text-ink-2">
                {turnError}
              </span>
              <Button
                size="icon"
                variant="quiet"
                className="-my-1 h-7 w-7 shrink-0"
                onClick={() => setTurnError(null)}
                aria-label="Fechar aviso"
              >
                <IconX className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        ) : null}

        <Composer
          providers={providers}
          credentials={credentials}
          providerKey={active?.provider_key ?? credentials[0]?.provider_key ?? null}
          model={active?.model ?? credentials[0]?.default_model ?? null}
          target={effectiveTarget}
          sending={streaming}
          onSend={send}
          onProviderChange={(providerKey, model) => {
            if (!conversationId) return;
            void mutateConversation(() =>
              api.patch(`/conversations/${conversationId}`, {
                provider_key: providerKey,
                model,
              }),
            );
          }}
          onTargetChange={(target) => {
            if (!conversationId) return;
            void mutateConversation(() =>
              api.patch(`/conversations/${conversationId}`, {
                target_default: target,
              }),
            );
          }}
        />
      </main>
    </div>
  );
}
