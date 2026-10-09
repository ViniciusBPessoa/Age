/**
 * A lista de mensagens, com o progresso real do turno (§8.1).
 *
 * "As primeiras iterações de um turno são silenciosas (§5.1), então a
 * UI precisa mostrar progresso real, não um spinner":
 *
 *   Age  ⟳ lendo sua agenda…              <- tool_start: get_events
 *   Age  ⟳ procurando horários livres…    <- tool_start: find_free_slots
 *   Age  Olhei sua agenda e achei espaço  <- token
 *
 * E a parte que costuma ser esquecida: "todas permanecem visíveis
 * depois, recolhidas — serve de explicação do que o assistente
 * consultou para chegar na proposta". Apagar as linhas quando o texto
 * chega destruiria a única pista de como a resposta foi formada.
 */

"use client";

import { useEffect, useRef } from "react";

import { Spinner } from "@/components/ui";
import { timeOf } from "@/lib/format";
import type { Message } from "@/lib/types";

/** Uma tool que rodou neste turno, para a linha de progresso. */
export interface ToolTrace {
  name: string;
  label: string;
  done: boolean;
  ok?: boolean;
  summary?: string;
}

export function MessageList({
  messages,
  streaming,
  streamingText,
  traces,
  timezone,
  children,
}: {
  messages: Message[];
  streaming: boolean;
  streamingText: string;
  traces: ToolTrace[];
  timezone: string;
  /** Revisão de plano, que aparece inline no chat (§8.1). */
  children?: React.ReactNode;
}) {
  const bottom = useRef<HTMLDivElement>(null);
  const container = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);

  // Rola para baixo só se a pessoa já estava no fim. Rolar à força
  // enquanto alguém lê uma mensagem antiga é como se perde o lugar no
  // meio de um texto que está chegando.
  useEffect(() => {
    const el = container.current;
    if (!el) return;
    const onScroll = () => {
      pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    if (pinned.current) {
      bottom.current?.scrollIntoView({ block: "end" });
    }
  }, [messages.length, streamingText, traces.length]);

  return (
    <div ref={container} className="flex-1 overflow-y-auto px-4 py-5">
      <ul className="mx-auto max-w-3xl space-y-5">
        {messages.map((message) => (
          <li key={message.id}>
            <Bubble
              role={message.role}
              text={message.content ?? ""}
              at={timeOf(message.created_at, timezone)}
            />
          </li>
        ))}

        {/* Turno em andamento: as linhas de tool e o texto que chega. */}
        {streaming || traces.length > 0 || streamingText ? (
          <li>
            <div className="text-[11px] font-semibold uppercase tracking-wide text-[var(--muted)]">
              Age
            </div>

            {traces.length > 0 ? (
              <ul className="mt-1.5 space-y-1" aria-label="O que o Age consultou">
                {traces.map((trace, index) => (
                  <li
                    key={`${trace.name}-${index}`}
                    className="flex items-center gap-2 text-xs text-[var(--muted)]"
                  >
                    {trace.done ? (
                      <span
                        aria-hidden
                        className={
                          trace.ok === false ? "text-[var(--warn)]" : "text-[var(--ok)]"
                        }
                      >
                        {trace.ok === false ? "!" : "✓"}
                      </span>
                    ) : (
                      <Spinner />
                    )}
                    <span>
                      {trace.label}
                      {trace.done ? "" : "…"}
                    </span>
                    {trace.summary ? (
                      <span className="truncate text-[var(--muted)] opacity-70">
                        — {trace.summary}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : null}

            {/* `aria-live="polite"` (§8.2): o leitor de tela anuncia o
                texto conforme chega, sem interromper o que já estava
                sendo lido. */}
            {streamingText ? (
              <p
                aria-live="polite"
                className={`mt-2 whitespace-pre-wrap text-sm leading-relaxed ${streaming ? "age-caret" : ""}`}
              >
                {streamingText}
              </p>
            ) : streaming && traces.length === 0 ? (
              <p className="mt-2 flex items-center gap-2 text-xs text-[var(--muted)]">
                <Spinner /> pensando…
              </p>
            ) : null}
          </li>
        ) : null}

        {children ? <li>{children}</li> : null}
      </ul>
      <div ref={bottom} />
    </div>
  );
}

function Bubble({
  role,
  text,
  at,
}: {
  role: Message["role"];
  text: string;
  at: string;
}) {
  // Mensagem de `tool` e `system` não aparece: é ruído de
  // implementação, e o que o usuário precisa saber sobre as tools já
  // está nas linhas de progresso.
  if (role === "tool" || role === "system") return null;

  if (role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-[var(--panel-2)] px-3.5 py-2.5">
          <p className="whitespace-pre-wrap text-sm leading-relaxed">{text}</p>
          <p className="mt-1 text-right text-[10px] text-[var(--muted)]">{at}</p>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-wide text-[var(--muted)]">
        Age <span className="font-normal normal-case">· {at}</span>
      </div>
      <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed">{text}</p>
    </div>
  );
}

/**
 * Estado vazio do chat (§8.1).
 *
 * "Chat em branco não comunica o que o produto faz." Os quatro exemplos
 * são escolhidos para cobrir as capacidades mais distintivas — estudo,
 * rotina, tarefa e diagnóstico de carga — e não quatro variações da
 * mesma coisa.
 *
 * A linha de baixo resolve um problema real: a pessoa precisa saber
 * **em qual agenda** o assistente vai escrever antes de pedir algo. Sem
 * isso ela aprova um plano e só depois descobre que foi para o lugar
 * errado.
 */
const EXAMPLES = [
  { icon: "📚", text: "preciso estudar cálculo II, prova dia 20" },
  { icon: "🔁", text: "quero academia 3x por semana" },
  { icon: "📋", text: "o que eu tenho pra fazer essa semana?" },
  { icon: "⚖️", text: "minha semana que vem tá pesada?" },
];

export function ChatEmptyState({
  calendarName,
  onPick,
}: {
  calendarName: string;
  onPick: (text: string) => void;
}) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center px-5 py-10">
      <h2 className="mb-6 text-base font-medium">O que você quer organizar?</h2>

      <ul className="grid w-full max-w-xl gap-2 sm:grid-cols-2">
        {EXAMPLES.map((example) => (
          <li key={example.text}>
            <button
              onClick={() => onPick(example.text)}
              className="h-full w-full rounded-lg border border-[var(--border)] bg-[var(--panel)] px-4 py-3 text-left text-sm leading-snug text-[var(--muted)] transition-colors hover:border-[var(--accent)] hover:text-[var(--text)]"
            >
              <span aria-hidden className="mr-1.5">
                {example.icon}
              </span>
              &ldquo;{example.text}&rdquo;
            </button>
          </li>
        ))}
      </ul>

      <p className="mt-6 text-xs text-[var(--muted)]">Sua agenda: 📅 {calendarName}</p>
    </div>
  );
}
