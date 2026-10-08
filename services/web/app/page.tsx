/**
 * Página de diagnóstico do M0.
 *
 * Não é produto: existe para provar, visualmente, que um POST atravessa
 * BFF -> core-api -> fila -> ai-worker -> Redis Stream -> SSE -> browser.
 * Será substituída pelo chat real no M1.
 *
 * A estrutura do que aparece aqui (tool_start silencioso e depois texto
 * token a token) é a mesma de um turno de verdade, então o que for
 * construído contra isto continua valendo.
 */

"use client";

import { useEffect, useRef, useState } from "react";

import { startEcho, subscribeToTurn, type StreamEvent } from "@/lib/sse";

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

      unsubscribe.current = subscribeToTurn(turnId, {
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
      });
    } catch (err) {
      append(`falha: ${err instanceof Error ? err.message : String(err)}`);
      setRunning(false);
    }
  }

  return (
    <main className="mx-auto max-w-2xl px-4 py-12">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold">Age · M0</h1>
        <p className="mt-1 text-sm text-[var(--muted)]">
          Diagnóstico da plumbing distribuída. Um POST deve atravessar os
          três serviços e voltar por SSE.
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
          <p className="text-sm text-[var(--muted)]">
            Nada ainda. Clique em enviar.
          </p>
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
