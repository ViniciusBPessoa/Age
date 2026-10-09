/**
 * Os tipos da fronteira TS↔Python.
 *
 * ARQUITETURA.md §2.5 prevê `age-types` **gerado** do OpenAPI, por
 * `make gen-types`, para que assinatura de endpoint não divergir em
 * silêncio. Isto aqui é escrito à mão, e a diferença merece ser dita em
 * vez de escondida:
 *
 * O gerador produz tipos fiéis mas inomináveis (`components["schemas"]
 * ["TaskOut"]`), e introduz uma etapa de build que precisa do core-api
 * de pé para rodar — o que significa que um `npm run typecheck` num
 * clone novo falharia por motivo não relacionado ao código.
 *
 * O custo assumido: estes tipos podem divergir do backend sem o build
 * reclamar. O que contém isso é `tests/contract.test.ts`, que lê o
 * OpenAPI do core-api de pé e compara campo a campo com o que está
 * declarado aqui — a divergência é pega por teste em vez de por
 * compilador. É uma rede mais fraca que a do gerador, e está
 * registrada como tal.
 *
 * Escrever estes tipos "de cabeça" já custou: a primeira versão
 * inventou `Credential.is_default`, `Preferences.timezone` e um
 * `PrivacyLimits.limits: string[]` que no backend são três campos
 * nomeados. Nenhum deles existia. Foi o que motivou o teste.
 *
 * Nenhum tipo aqui inventa campo: cada um corresponde a um `*Out` de
 * `core-api/app/api/*.py`. Datas chegam como string ISO 8601 — o front
 * só formata, porque todo cálculo de tempo acontece no backend (§9.5).
 */

// ---------------------------------------------------------------
// Vocabulário do domínio
// ---------------------------------------------------------------

/** §5.6: o que pode ser mexido num replanejamento. */
export type Flexibility = "fixed" | "semi_flexible" | "flexible";

/** §5.7.2: para onde vai o que o assistente propõe. */
export type Target = "event" | "task" | "both" | "ask";

export type TaskStatus = "open" | "done" | "cancelled";
export type Priority = "low" | "normal" | "high" | "urgent";
export type OccurrenceStatus = "planned" | "done" | "skipped" | "moved";
export type PlanStatus =
  | "proposed"
  | "applied"
  | "partially_applied"
  | "rejected"
  | "expired";

/** §5.7: as operações que um item de plano pode carregar. */
export type PlanOp = "create" | "move" | "resize" | "delete" | "complete" | "reorder";

export interface ApiError {
  error: { code: string; message: string; retryable?: boolean };
}

// ---------------------------------------------------------------
// Conta
// ---------------------------------------------------------------
export interface Me {
  id: string;
  email: string;
  name: string | null;
  timezone: string;
  locale: string;
  role: string;
  email_verified: boolean;
  /** §8.1: 0..3, persistido para o onboarding poder ser retomado. */
  onboarding_step: number;
}

export interface SessionInfo {
  id: string;
  device_label: string | null;
  ip: string | null;
  created_at: string;
  last_used_at: string | null;
  is_current: boolean;
}

// ---------------------------------------------------------------
// LLM (§3.1)
// ---------------------------------------------------------------
export interface ProviderInfo {
  key: string;
  name: string;
  models: string[];
  default_model: string;
}

/** Note a ausência de `api_key`: é deliberada no backend (§4.3). */
export interface Credential {
  id: string;
  provider_key: string;
  key_last4: string;
  default_model: string | null;
  base_url: string | null;
  label: string | null;
  is_active: boolean;
  last_verified_at: string | null;
  last_error: string | null;
  created_at: string;
}

export interface VerifyResult {
  ok: boolean;
  error_code: string | null;
  message: string | null;
  capabilities: Record<string, unknown> | null;
}

// ---------------------------------------------------------------
// Agenda (§3.2)
// ---------------------------------------------------------------
export interface CalendarRef {
  id: string;
  name: string;
  timezone: string;
  is_primary: boolean;
  writable: boolean;
  color: string | null;
}

export interface AgeEvent {
  id: string;
  calendar_id: string;
  title: string;
  start: string;
  end: string;
  all_day: boolean;
  description: string | null;
  location: string | null;
  recurrence: string | null;
  flexibility: Flexibility;
  created_by_age: boolean;
  etag: string | null;
}

export interface Slot {
  start: string;
  end: string;
  score: number;
  reasons: string[];
}

// ---------------------------------------------------------------
// Tarefas (§3.3)
// ---------------------------------------------------------------
export interface TaskListRef {
  id: string;
  name: string;
  is_default: boolean;
  writable: boolean;
  color: string | null;
}

/** §5.7.1: o bloco de tempo que reserva uma tarefa. */
export interface Reserved {
  event_ref_kind: string;
  event_ref_id: string;
  title: string;
  start: string;
  end: string;
  minutes: number;
}

export interface Task {
  id: string;
  ref_kind: string;
  list_id: string;
  title: string;
  notes: string | null;
  due_date: string | null;
  due_at: string | null;
  status: TaskStatus;
  completed_at: string | null;
  priority: Priority | null;
  labels: string[];
  estimated_min: number | null;
  position: number | null;
  is_overdue: boolean;
  reserved: Reserved[];
  reserved_minutes: number;
  provider_name: string | null;
  /** §3.3.2: campos que o provedor não guardou. A UI mostra no item. */
  dropped_fields: string[];
}

export interface TaskOverview {
  total_open: number;
  overdue: Task[];
  due_soon: Task[];
  without_due_count: number;
  unscheduled_due_soon: Task[];
  estimated_minutes_unscheduled: number;
  summary_text: string;
}

// ---------------------------------------------------------------
// Planos (§5.7)
// ---------------------------------------------------------------
export interface Conflict {
  kind: string;
  message: string;
  severity: string;
}

export interface PlanItem {
  id: string;
  target: "event" | "task";
  op: PlanOp;
  reason: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  conflicts: Conflict[];
  /** §5.7.1: o par tarefa+evento se aprova e se desfaz junto. */
  links: string[];
  status: string;
  is_blocking: boolean;
}

export interface Plan {
  id: string;
  conversation_id: string | null;
  origin_note: string | null;
  status: PlanStatus;
  summary: string;
  items: PlanItem[];
  created_at: string;
  expires_at: string;
  is_expired: boolean;
}

export interface ApplyResult {
  status: string;
  applied_count: number;
  failed: { item_id?: string; reason?: string }[];
  manual_fix_needed: string[];
  message: string;
}

// ---------------------------------------------------------------
// Conversas (§6.2)
// ---------------------------------------------------------------
export interface Conversation {
  id: string;
  title: string | null;
  title_is_auto: boolean;
  provider_key: string | null;
  model: string | null;
  target_default: Target;
  pinned_at: string | null;
  archived_at: string | null;
  last_message_at: string | null;
  message_count: number;
  created_at: string;
  /**
   * §8.1: quantos eventos e tarefas esta conversa colocou na vida da
   * pessoa — o `8📅` / `6📋` da sidebar.
   *
   * Conta item com `status = 'applied'`, não plano aplicado: aprovação
   * é granular (§5.8), então um plano parcialmente aplicado tem itens
   * dentro e itens fora, e só os primeiros entraram na agenda.
   */
  applied_events: number;
  applied_tasks: number;
}

export interface Message {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string | null;
  provider_key: string | null;
  model: string | null;
  created_at: string;
}

export interface TurnAccepted {
  turn_id: string;
  message_id: string;
  stream_url: string;
}

// ---------------------------------------------------------------
// Estudo (§5.9.3)
// ---------------------------------------------------------------
export interface Subject {
  id: string;
  name: string;
  deadline: string | null;
  status: string;
  total_estimated_min: number | null;
  sessions_planned: number;
  sessions_done: number;
}

export interface SubjectProgress {
  subject_id: string;
  name: string;
  deadline: string | null;
  total_estimated_min: number;
  total_done_min: number;
  sessions_planned: number;
  sessions_done: number;
  sessions_skipped: number;
  topics_total: number;
  topics_completed: number;
  completion: number;
}

/** §8.1 "marcação rápida": sessão que passou e não foi marcada. */
export interface PendingSession {
  id: string;
  topic: string;
  subject: string;
  scheduled_start: string;
  scheduled_end: string;
  kind: string;
  part: number | null;
  of_parts: number | null;
}

// ---------------------------------------------------------------
// Rotinas (§5.9.4)
// ---------------------------------------------------------------
export interface Routine {
  id: string;
  title: string;
  rrule: string;
  duration_min: number;
  flexibility: Flexibility;
  target_per_week: number | null;
  category: string | null;
  started_at: string;
  paused_at: string | null;
  ended_at: string | null;
  is_active: boolean;
  next_occurrence: string | null;
}

export interface Occurrence {
  routine_id: string;
  routine_title: string;
  start: string;
  end: string;
  status: OccurrenceStatus;
  actual_min: number | null;
  moved_to: string | null;
}

/**
 * §5.9.4: aderência **sem streak**, e a ausência é o ponto.
 *
 * Não existe campo de ofensiva aqui porque não existe no backend, e não
 * existe no backend por decisão de produto: streak transforma uma falta
 * em perda de progresso, e aí um dia ruim faz a pessoa abandonar a
 * série inteira. Um teste no core-api cobra essa ausência.
 *
 * `moved` é contado separado de `skipped` pelo mesmo raciocínio: quem
 * mudou a academia de terça para quinta e foi, cumpriu.
 */
export interface Adherence {
  routine_id: string | null;
  routine_title: string;
  planned: number;
  done: number;
  skipped: number;
  moved: number;
  total_minutes_done: number;
  target_per_week: number | null;
  per_week: number;
  completion: number;
  meets_target: boolean | null;
}

export interface RoutineConflict {
  start: string;
  end: string;
  with_title: string;
  blocking: boolean;
}

export interface RoutinePreview {
  occurrences: Occurrence[];
  conflicts: RoutineConflict[];
  blocking_count: number;
}

// ---------------------------------------------------------------
// Preferências (§5.4)
// ---------------------------------------------------------------
export type AutonomyLevel = "propose" | "auto_create" | "auto_all";

export interface Preferences {
  working_hours: Record<string, unknown> | null;
  blocked_windows: Record<string, unknown> | null;
  /** Lista de faixas, não mapa por dia: a preferência de horário de
   * estudo é a mesma em qualquer dia, ao contrário de `working_hours`. */
  preferred_study_times: unknown[] | null;
  session_min: number;
  session_max: number;
  break_min: number;
  max_hours_per_day: number;
  buffer_min: number;
  autonomy_level: AutonomyLevel;
  spaced_repetition_enabled: boolean;
  default_target: Target;
  private_mode: boolean;
}

// ---------------------------------------------------------------
// Privacidade (§6.2.5, §9.6)
// ---------------------------------------------------------------
/**
 * Os três limites honestos de §6.2.5, vindos da API.
 *
 * O texto NÃO é duplicado na tela: vem daqui. O backend os mantém em
 * constantes de módulo justamente para que tela e resposta não
 * divirjam, e repetir as frases no componente desfaria isso.
 */
export interface PrivacyLimits {
  llm: string;
  backup: string;
  external_events: string;
  undo_window_days: number;
}

export interface ExportCounts {
  conversations: number;
  messages: number;
  events: number;
  tasks: number;
  routines: number;
  study_subjects: number;
  plans: number;
}

export interface DeletionPreview {
  scope: string;
  will_delete: ExportCounts;
  will_preserve: string[];
  limits: PrivacyLimits;
  undo_until: string;
  external_events_created: number;
}

// ---------------------------------------------------------------
// Relatório (§5.9.9)
// ---------------------------------------------------------------
/**
 * Planejado vs. realizado, com `summary_text` pronto do backend.
 *
 * A UI mostra `summary_text` em vez de montar a frase: §5.9.9 proíbe
 * elogio vazio ("parabéns" com 2 de 6 sessões feitas destrói a
 * confiança em todo o resto), e essa regra é do domínio. Remontar o
 * texto no componente seria reabrir a decisão onde ela não é testada.
 */
export interface PeriodReport {
  period_start: string;
  period_end: string;
  study_planned: number;
  study_done: number;
  study_skipped: number;
  study_planned_minutes: number;
  study_done_minutes: number;
  study_completion: number;
  routines: Adherence[];
  events_total: number;
  patterns: string[];
  summary_text: string;
}

// ---------------------------------------------------------------
// Mudança externa (§5.10)
// ---------------------------------------------------------------
export interface ExternalChange {
  id: string;
  change_kind: string;
  title: string | null;
  starts_at: string | null;
  ends_at: string | null;
  conflicts_with_title: string | null;
  detected_at: string;
  is_conflict: boolean;
}

/**
 * §5.10: reativo, nunca notificação.
 *
 * O front chama ao abrir o chat e mostra `summary_text` uma vez. O
 * `acknowledge=true` é o que impede a mesma reunião de ser anunciada em
 * toda mensagem — sem ele o produto deixaria de ser reativo e passaria
 * a perseguir o usuário.
 */
export interface ExternalChanges {
  changes: ExternalChange[];
  conflict_count: number;
  summary_text: string;
}
