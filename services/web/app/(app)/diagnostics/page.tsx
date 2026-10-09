/**
 * Página de diagnóstico da plumbing distribuída.
 *
 * Nasceu no M0 como a tela principal, com a nota de que seria
 * "substituída pelo chat real". Ela foi — o chat está em `/chat`. Mas
 * ela não foi apagada, e vale dizer por quê: é a única forma de provar
 * que um POST atravessa BFF -> core-api -> fila -> ai-worker -> Redis
 * Stream -> SSE -> browser **sem gastar token de LLM**.
 *
 * Quando o chat não responde, a primeira pergunta é se o problema é o
 * modelo ou o encanamento. Esta tela responde isso em um clique, e
 * manter um caminho de teste que não depende de credencial de terceiro
 * vale mais que a página a menos.
 */

"use client";

import { useEffect, useRef, useState } from "react";

import { subscribeToTurn, type StreamEvent } from "@/lib/sse";

/**
 * Dispara um turno de eco.
 *
 * Vive aqui, e não em `lib/sse.ts`, porque `/echo` é rota de
 * diagnóstico: deixá-la no módulo compartilhado convidaria o código de
 * produto a chamá-la por engano.
 */
async function startEcho(message: string): Promise<string> {
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

type Health = { ready: boolean; checks: Record<string, string> } | null;

export default function Page() {
  const [message, setMessage] = useState("teste de plumbing");
  const [log, setLog] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [running, setRunning] = useState(false);
  const [health, setHealth] = useState<Health>(null);
  const unsubscribe = useRef<(() => void) | null>(null);

  useEffect(() => {
    // Rota de página no Next (não o BFF), que consulta o /readyz do
    // core-api server-side. O browser não alcança o core-api direto.
    fetch("/status")
      .then((r) => (r.ok ? r.json() : null))
      .then(setHealth)
      .catch(() => setHealth(null));
    return () => unsubscribe.current?.();
  }, []);

  const append = (line: string) =>
    setLog((prev) => [...prev, `${new Date().toLocaleTimeString()}  ${line}`]);

  async function run() {
    setLog([]);
    setText("");
    setRunning(true);
    try {
      const turnId = await startEcho(message);
      append(`202 aceito · turn_id=${turnId}`);

      unsubscribe.current = subscribeToTurn(
        turnId,
        {
          onEvent: (e: StreamEvent) => {
            switch (e.type) {
              case "tool_start":
                append(`⟳ ${e.name}…`);
                break;
              case "tool_end":
                append(`✓ ${e.name} — ${e.summary ?? "ok"}`);
                break;
              case "token":
                setText((prev) => prev + e.delta);
                break;
              case "usage":
                append(`uso: ${e.provider_key}/${e.model}`);
                break;
              case "done":
                append(`fim · status=${e.status}`);
                setRunning(false);
                break;
              case "error":
                append(`erro ${e.code}: ${e.message}`);
                setRunning(false);
                break;
            }
          },
          onClose: () => setRunning(false),
          onError: (err) => {
            append(`falha no stream: ${String(err)}`);
            setRunning(false);
          },
        },
        "echo",
      );
    } catch (err) {
      append(`falha: ${err instanceof Error ? err.message : String(err)}`);
      setRunning(false);
    }
  }

  return (
    <main className="mx-auto max-w-2xl px-4 py-12">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold">Age · diagnóstico</h1>
        <p className="mt-1 text-sm text-[var(--muted)]">
          Diagnóstico da plumbing distribuída. Um POST deve atravessar os três serviços
          e voltar por SSE.
        </p>
      </header>

      <section
        className="mb-6 rounded-lg border p-4"
        style={{ borderColor: "var(--border)", background: "var(--panel)" }}
      >
        <label htmlFor="msg" className="block text-sm text-[var(--muted)]">
          Mensagem
        </label>
        <div className="mt-2 flex gap-2">
          <input
            id="msg"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            className="flex-1 rounded border bg-transparent px-3 py-2 text-sm outline-none focus:border-[var(--accent)]"
            style={{ borderColor: "var(--border)" }}
          />
          <button
            onClick={run}
            disabled={running || message.trim().length === 0}
            className="rounded px-4 py-2 text-sm font-medium disabled:opacity-40"
            style={{ background: "var(--accent)", color: "#06121f" }}
          >
            {running ? "rodando…" : "enviar"}
          </button>
        </div>
      </section>

      {text && (
        <section
          className="mb-6 rounded-lg border p-4"
          style={{ borderColor: "var(--border)", background: "var(--panel)" }}
        >
          <h2 className="mb-2 text-xs uppercase tracking-wide text-[var(--muted)]">
            Resposta (streaming)
          </h2>
          <p className="text-sm">{text}</p>
        </section>
      )}

      <section
        className="rounded-lg border p-4"
        style={{ borderColor: "var(--border)", background: "var(--panel)" }}
      >
        <h2 className="mb-2 text-xs uppercase tracking-wide text-[var(--muted)]">
          Eventos
        </h2>
        {log.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">Nada ainda. Clique em enviar.</p>
        ) : (
          <pre className="whitespace-pre-wrap text-xs leading-relaxed text-[var(--muted)]">
            {log.join("\n")}
          </pre>
        )}
      </section>

      {health && (
        <p className="mt-6 text-xs text-[var(--muted)]">
          core-api: {health.ready ? "pronto" : "indisponível"} ·{" "}
          {Object.entries(health.checks)
            .map(([k, v]) => `${k}=${v}`)
            .join(" · ")}
        </p>
      )}
    </main>
  );
}
