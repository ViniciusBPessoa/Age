/**
 * Cliente SSE sobre EventSource nativo.
 *
 * ARQUITETURA.md §7.1. O desenho de `POST` para criar o turno e `GET`
 * para abrir o stream é o que permite usar `EventSource` — que só faz
 * GET e não aceita cabeçalhos customizados. Aqui isso deixa de ser
 * limitação e passa a ser o desenho correto, porque o turno já existe
 * como recurso antes de o stream abrir.
 *
 * A retomada é o que torna isso robusto: os eventos ficam num Redis
 * Stream com ids sequenciais, então reconectar é reler do último id
 * visto. Perder o Wi-Fi no meio de um turno não perde o turno.
 */

export type StreamEvent =
  | { type: "token"; delta: string }
  | { type: "tool_start"; name: string }
  | { type: "tool_end"; name: string; ok: boolean; summary?: string }
  | { type: "plan"; plan_id: string; item_count: number }
  | {
      type: "usage";
      prompt_tokens: number;
      completion_tokens: number;
      model: string;
      provider_key: string;
    }
  | { type: "done"; message_id: string; status: string }
  | { type: "error"; code: string; message: string; retryable: boolean };

export interface StreamHandlers {
  onEvent: (event: StreamEvent) => void;
  onClose?: () => void;
  onError?: (err: unknown) => void;
}

export function subscribeToTurn(
  turnId: string,
  handlers: StreamHandlers,
): () => void {
  let lastEventId: string | null = null;
  let closed = false;
  let source: EventSource | null = null;

  const open = () => {
    if (closed) return;

    const url = new URL(`/api/echo/${turnId}/stream`, window.location.origin);
    if (lastEventId) url.searchParams.set("last_event_id", lastEventId);

    source = new EventSource(url);

    const handle = (e: MessageEvent) => {
      if (e.lastEventId) lastEventId = e.lastEventId;
      // `ping` é o heartbeat que atravessa proxies que derrubam conexão
      // ociosa — não carrega dado.
      if (!e.data) return;
      try {
        const event = JSON.parse(e.data) as StreamEvent;
        handlers.onEvent(event);
        if (event.type === "done" || event.type === "error") {
          closed = true;
          source?.close();
          handlers.onClose?.();
        }
      } catch (err) {
        handlers.onError?.(err);
      }
    };

    for (const name of [
      "token",
      "tool_start",
      "tool_end",
      "plan",
      "usage",
      "done",
      "error",
    ]) {
      source.addEventListener(name, handle);
    }
    source.onmessage = handle;

    source.onerror = () => {
      source?.close();
      if (closed) return;
      // EventSource reconecta sozinho, mas queremos controlar o
      // last_event_id na nova URL — então reabrimos manualmente.
      setTimeout(open, 1500);
    };
  };

  open();

  return () => {
    closed = true;
    source?.close();
  };
}

export async function startEcho(message: string): Promise<string> {
  const res = await fetch("/api/echo", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.error?.message ?? `Falhou com HTTP ${res.status}`);
  }
  const data = (await res.json()) as { turn_id: string };
  return data.turn_id;
}
