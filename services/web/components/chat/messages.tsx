/**
 * A lista de mensagens, com o progresso real do turno (§8.1).
 *
 * "As primeiras iterações de um turno são silenciosas (§5.1), então a
 * UI precisa mostrar progresso real, não um spinner":
 *
 *   ✦ Age
 *     ⟳ lendo sua agenda…              <- tool_start: get_events
 *     ⟳ procurando horários livres…    <- tool_start: find_free_slots
 *     Olhei sua agenda e achei espaço  <- token
 *
 * E a parte que costuma ser esquecida: "todas permanecem visíveis
 * depois, recolhidas — serve de explicação do que o assistente
 * consultou para chegar na proposta". Apagar as linhas quando o texto
 * chega destruiria a única pista de como a resposta foi formada.
 *
 * O "recolhidas" do texto acima virou um `<details>` de verdade: antes
 * as linhas ficavam todas abertas para sempre, e três turnos de
 * conversa enterravam as respostas no meio de passos de ferramenta.
 * Durante o turno ele abre sozinho (é o único progresso que existe) e
 * fecha quando o texto começa a chegar.
 */

"use client";

import { useEffect, useRef, useState } from "react";

import {
  IconCheck,
  IconAlert,
  IconChevronDown,
  IconSparkle,
  Spinner,
  cx,
} from "@/components/ui";
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

  const visible = messages.filter((m) => m.role === "user" || m.role === "assistant");

  return (
    /*
     * `justify-end` com `min-h-full` no miolo: a conversa fica
     * ancorada embaixo, junto do compositor, e so empurra para cima
     * quando passa da altura da tela.
     *
     * Sem isso, duas mensagens ficavam coladas no topo com um vazio de
     * 600px ate o campo de texto — e o olho procura a conversa onde
     * ela esta sendo escrita, nao no alto da pagina.
     */
    <div
      ref={container}
      className="flex flex-1 flex-col justify-end overflow-y-auto px-4 py-6 md:px-6"
    >
      <ul className="mx-auto w-full max-w-3xl space-y-7">
        {visible.map((message) => (
          <li key={message.id} className="animate-fade-up">
            <Bubble
              role={message.role}
              text={message.content ?? ""}
              at={timeOf(message.created_at, timezone)}
            />
          </li>
        ))}

        {/* Turno em andamento: as linhas de tool e o texto que chega. */}
        {streaming || traces.length > 0 || streamingText ? (
          <li className="animate-fade-up">
            <div className="flex gap-3">
              <Avatar />
              <div className="min-w-0 flex-1 space-y-2.5 pt-0.5">
                {traces.length > 0 ? (
                  <ToolTrail traces={traces} collapsed={streamingText.length > 0} />
                ) : null}

                {/* `aria-live="polite"` (§8.2): o leitor de tela anuncia
                    o texto conforme chega, sem interromper o que já
                    estava sendo lido. */}
                {streamingText ? (
                  <div
                    aria-live="polite"
                    className={cx(
                      "whitespace-pre-wrap text-base leading-[1.7] text-ink-2",
                      streaming && "age-caret",
                    )}
                  >
                    {streamingText}
                  </div>
                ) : streaming && traces.length === 0 ? (
                  <ThinkingDots />
                ) : null}
              </div>
            </div>
          </li>
        ) : null}

        {children ? <li className="animate-fade-up">{children}</li> : null}
      </ul>
      <div ref={bottom} className="h-2" />
    </div>
  );
}

function Avatar() {
  return (
    <span
      aria-hidden
      className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-brand to-proposed text-white"
    >
      <IconSparkle className="h-[15px] w-[15px]" />
    </span>
  );
}

/**
 * "Pensando" como três pontos, não como spinner.
 *
 * Um spinner diz "travado"; os pontos em cascata dizem "em curso".
 * É a convenção de todo chat, e a diferença de percepção é real.
 */
function ThinkingDots() {
  return (
    <p className="flex items-center gap-1.5 py-1 text-sm text-muted">
      <span className="sr-only">O Age está pensando…</span>
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          aria-hidden
          className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted"
          style={{ animationDelay: `${i * 140}ms`, animationDuration: "1s" }}
        />
      ))}
    </p>
  );
}

/**
 * O rastro de ferramentas do turno (§8.1).
 *
 * `open` controlado em vez de `<details open>` solto: ele precisa abrir
 * durante o turno e recolher quando a resposta chega, e um atributo
 * estático não faria as duas coisas.
 */
function ToolTrail({ traces, collapsed }: { traces: ToolTrace[]; collapsed: boolean }) {
  const [open, setOpen] = useState(true);
  const touched = useRef(false);

  // Recolhe sozinho quando o texto começa, a menos que a pessoa tenha
  // mexido no controle — nesse caso a escolha dela manda.
  useEffect(() => {
    if (collapsed && !touched.current) setOpen(false);
  }, [collapsed]);

  const running = traces.some((t) => !t.done);
  const failed = traces.filter((t) => t.ok === false).length;

  return (
    <div className="overflow-hidden rounded-xl border border-line bg-surface/60">
      <button
        onClick={() => {
          touched.current = true;
          setOpen((o) => !o);
        }}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-muted transition-colors hover:bg-surface-2/60 hover:text-ink"
      >
        {running ? (
          <Spinner className="h-3 w-3" />
        ) : failed > 0 ? (
          <IconAlert className="h-3.5 w-3.5 text-warn" />
        ) : (
          <IconCheck className="h-3.5 w-3.5 text-ok" />
        )}
        <span className="flex-1 font-medium">
          {running
            ? traces[traces.length - 1].label
            : `${traces.length} ${traces.length === 1 ? "consulta" : "consultas"}`}
          {running ? "…" : ""}
        </span>
        <IconChevronDown
          className={cx(
            "h-3.5 w-3.5 transition-transform duration-200",
            open && "rotate-180",
          )}
        />
      </button>

      {open ? (
        <ul
          aria-label="O que o Age consultou"
          className="space-y-1.5 border-t border-line px-3 py-2.5"
        >
          {traces.map((trace, index) => (
            <li
              key={`${trace.name}-${index}`}
              className="flex items-start gap-2 text-xs leading-relaxed"
            >
              <span className="mt-[3px] shrink-0">
                {trace.done ? (
                  trace.ok === false ? (
                    <IconAlert className="h-3 w-3 text-warn" />
                  ) : (
                    <IconCheck className="h-3 w-3 text-ok" />
                  )
                ) : (
                  <Spinner className="h-3 w-3" />
                )}
              </span>
              <span className="min-w-0">
                <span className="text-ink-2">{trace.label}</span>
                {trace.summary ? (
                  <span className="text-muted"> — {trace.summary}</span>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
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
  if (role === "user") {
    /*
     * A mensagem do usuário é bolha; a do Age é texto corrido.
     *
     * É a assimetria que Claude, ChatGPT e Gemini usam, e por um motivo
     * concreto: a resposta é o conteúdo longo da tela, e encaixotá-la
     * estreita a linha e piora a leitura. A bolha serve ao texto curto.
     */
    return (
      <div className="flex justify-end">
        <div className="group max-w-[82%]">
          <div className="rounded-2xl rounded-br-md bg-brand-soft px-4 py-2.5 shadow-ring">
            <p className="whitespace-pre-wrap text-base leading-relaxed text-ink">
              {text}
            </p>
          </div>
          {/* A hora só no hover: ela importa raramente, e exibi-la
              sempre adiciona uma linha de ruído por mensagem. */}
          <time className="mt-1 block pr-1 text-right text-2xs text-faint opacity-0 transition-opacity group-hover:opacity-100">
            {at}
          </time>
        </div>
      </div>
    );
  }

  return (
    <div className="group flex gap-3">
      <Avatar />
      <div className="min-w-0 flex-1 pt-0.5">
        <p className="whitespace-pre-wrap text-base leading-[1.7] text-ink-2">{text}</p>
        <time className="mt-1.5 block text-2xs text-faint opacity-0 transition-opacity group-hover:opacity-100">
          {at}
        </time>
      </div>
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
  {
    text: "preciso estudar cálculo II, prova dia 20",
    hint: "divide a matéria em sessões até a data",
  },
  {
    text: "quero academia 3x por semana",
    hint: "cria uma rotina e acha os horários",
  },
  {
    text: "o que eu tenho pra fazer essa semana?",
    hint: "lê a agenda e a lista de tarefas",
  },
  {
    text: "minha semana que vem tá pesada?",
    hint: "mede a carga e diz onde aperta",
  },
];

export function ChatEmptyState({
  calendarName,
  onPick,
}: {
  calendarName: string;
  onPick: (text: string) => void;
}) {
  return (
    <div className="aurora flex flex-1 flex-col items-center justify-center px-5 py-12">
      <div className="w-full max-w-2xl">
        <div className="mb-8 text-center">
          <h2 className="text-2xl font-semibold tracking-[-0.02em]">
            O que você quer organizar?
          </h2>
          <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-muted">
            Descreva em palavras suas. O Age lê sua agenda, propõe um plano e espera
            você aprovar antes de escrever qualquer coisa.
          </p>
        </div>

        <ul className="stagger grid gap-2.5 sm:grid-cols-2">
          {EXAMPLES.map((example, i) => (
            <li key={example.text} style={{ ["--i" as string]: i }}>
              <button
                onClick={() => onPick(example.text)}
                className="card card-hover group h-full w-full px-4 py-3.5 text-left"
              >
                <span className="block text-sm font-medium leading-snug text-ink-2 transition-colors group-hover:text-ink">
                  &ldquo;{example.text}&rdquo;
                </span>
                <span className="mt-1.5 block text-xs text-muted">{example.hint}</span>
              </button>
            </li>
          ))}
        </ul>

        <p className="mt-7 flex items-center justify-center gap-1.5 text-xs text-muted">
          <span className="h-1.5 w-1.5 rounded-full bg-ok" aria-hidden />
          Vai escrever em <span className="font-medium text-ink-2">{calendarName}</span>
        </p>
      </div>
    </div>
  );
}
