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
 *
 * O `EventSource` não manda `Authorization`, e não precisa: o cookie
 * `age_access` é same-origin e vai sozinho, e o BFF o converte em
 * Bearer (§4.1.1). É outra razão para o BFF existir.
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

const EVENT_NAMES = [
  "token",
  "tool_start",
  "tool_end",
  "plan",
  "usage",
  "done",
  "error",
] as const;

/**
 * Assina o stream de um turno.
 *
 * `basePath` existe porque o M0 deixou uma rota de diagnóstico
 * (`/echo/{id}/stream`) que tem o mesmo protocolo da rota real
 * (`/turns/{id}/stream`). A página de diagnóstico continua útil para
 * provar a plumbing sem gastar token de LLM.
 */
export function subscribeToTurn(
  turnId: string,
  handlers: StreamHandlers,
  basePath: "turns" | "echo" = "turns",
): () => void {
  let lastEventId: string | null = null;
  let closed = false;
  let source: EventSource | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let attempts = 0;

  const open = () => {
    if (closed) return;

    const url = new URL(`/api/${basePath}/${turnId}/stream`, window.location.origin);
    if (lastEventId) url.searchParams.set("last_event_id", lastEventId);

    source = new EventSource(url);

    const handle = (e: MessageEvent) => {
      if (e.lastEventId) lastEventId = e.lastEventId;
      // `ping` é o heartbeat que atravessa proxies que derrubam conexão
      // ociosa — não carrega dado.
      if (!e.data) return;
      // Chegou evento: a conexão está boa, então o backoff reseta. Sem
      // isto, uma sessão longa com uma queda no início ficaria esperando
      // 8 s para reconectar de uma queda que acontecesse uma hora depois.
      attempts = 0;
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

    for (const name of EVENT_NAMES) {
      source.addEventListener(name, handle);
    }
    source.onmessage = handle;

    source.onerror = () => {
      source?.close();
      if (closed) return;
      // EventSource reconecta sozinho, mas queremos controlar o
      // last_event_id na nova URL — então reabrimos manualmente.
      //
      // Com backoff: reconectar a cada 1,5 s para sempre significa que
      // um worker fora do ar vira uma enxurrada de requests do browser,
      // que é justamente o que não se quer durante um incidente.
      attempts += 1;
      const delay = Math.min(1500 * 2 ** (attempts - 1), 15_000);
      retryTimer = setTimeout(open, delay);
    };
  };

  open();

  return () => {
    closed = true;
    if (retryTimer) clearTimeout(retryTimer);
    source?.close();
  };
}

/**
 * Nomes de tool em linguagem de gente (§8.1).
 *
 *   Age  ⟳ lendo sua agenda…        <- tool_start: get_events
 *   Age  ⟳ procurando horários…     <- tool_start: find_free_slots
 *
 * Existe porque as primeiras iterações de um turno são silenciosas
 * (§5.1): sem isto a UI mostraria um spinner por vários segundos, e
 * spinner não diz nada sobre o que está sendo feito. Mostrar o nome cru
 * da função (`find_free_slots`) seria pior que não mostrar — expõe a
 * implementação e ainda não informa.
 *
 * Nome desconhecido cai num texto genérico, nunca no nome cru: uma tool
 * nova no worker não deve vazar `reorder_task_list` para a tela.
 */
const TOOL_LABELS: Record<string, string> = {
  // Agenda (§5.2)
  get_events: "lendo sua agenda",
  get_pending_proposals: "vendo o que espera aprovação",
  find_free_slots: "procurando horários livres",
  summarize_workload: "medindo a carga da semana",
  propose_event: "montando a proposta",
  propose_recurring_event: "montando a série",
  propose_move: "remanejando",
  propose_delete: "preparando a remoção",
  // Estudo (§5.9.3)
  propose_study_plan: "montando o plano de estudo",
  get_study_progress: "vendo seu progresso",
  mark_study_session: "registrando a sessão",
  // Tarefas (§5.9.5)
  get_tasks: "lendo sua lista",
  get_task_lists: "vendo suas listas",
  get_task_overview: "cruzando prazos e tempo reservado",
  create_task: "preparando a tarefa",
  update_task: "ajustando a tarefa",
  delete_task: "removendo da lista",
  complete_task: "concluindo",
  reorder_tasks: "reordenando",
  reserve_time_for_task: "reservando tempo",
  // Rotinas (§5.9.4)
  propose_routine: "simulando a série",
  get_routines: "lendo suas rotinas",
  get_routine_adherence: "calculando a aderência",
  mark_routine_occurrence: "registrando",
  get_period_report: "levantando os números do período",
  // Externo (§5.10) e preferências
  get_external_changes: "vendo o que mudou por fora",
  get_user_preferences: "conferindo suas preferências",
  save_preference: "guardando a preferência",
};

export function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? "consultando seus dados";
}
