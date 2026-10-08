/**
 * BFF — repasse puro para o core-api.
 *
 * ARQUITETURA.md §2.4.1. Este arquivo é a única ponte entre o browser e o
 * backend, e a regra sobre ele é verificada em CI (§10.2):
 *
 *   > As rotas de API do Next funcionam SÓ como BFF: anexar o cookie de
 *   > sessão, repassar ao core-api, devolver. Nenhuma regra de
 *   > agendamento, nenhum acesso a banco, nenhuma chamada a provedor de
 *   > LLM ou de calendário.
 *
 * Ele existe por dois motivos bons, não por preguiça:
 *   1. O token fica fora do JavaScript do navegador (cookie httpOnly
 *      lido só no servidor).
 *   2. CORS deixa de existir: todo request sai do mesmo origin.
 *
 * O ponto delicado é o streaming. Se este handler fizer
 * `await response.text()`, o SSE morre sem erro nenhum — a resposta
 * chega inteira no final e parece que o streaming nunca foi
 * implementado (§7.1). Por isso o body é repassado como ReadableStream.
 */

import { type NextRequest } from "next/server";

// Node, não edge: stream longo tem comportamento mais previsível aqui.
export const runtime = "nodejs";
// Sem isto, o Next tenta cachear e o stream quebra.
export const dynamic = "force-dynamic";

const CORE_API_URL = process.env.CORE_API_URL ?? "http://core-api:8000";

/** Cabeçalhos que não devem atravessar o proxy. */
const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "transfer-encoding",
  "upgrade",
  "host",
  "content-length",
]);

function forwardHeaders(req: NextRequest): Headers {
  const headers = new Headers();
  req.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) headers.set(key, value);
  });
  // Rastreabilidade ponta a ponta: o trace_id nasce aqui e é propagado
  // até o worker, inclusive dentro do payload da fila (§12).
  if (!headers.has("x-trace-id")) {
    headers.set("x-trace-id", crypto.randomUUID());
  }
  return headers;
}

async function proxy(req: NextRequest, path: string[]): Promise<Response> {
  const target = new URL(`/api/v1/${path.join("/")}`, CORE_API_URL);
  target.search = req.nextUrl.search;

  const isStream = path.includes("stream");

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: req.method,
      headers: forwardHeaders(req),
      // GET/HEAD não podem ter body.
      body: req.method === "GET" || req.method === "HEAD" ? undefined : req.body,
      // duplex é obrigatório para repassar body como stream.
      ...(req.method !== "GET" && req.method !== "HEAD"
        ? { duplex: "half" as const }
        : {}),
      // Nunca cachear: nem resposta de API, nem stream.
      cache: "no-store",
      redirect: "manual",
    });
  } catch {
    return Response.json(
      {
        error: {
          code: "upstream_unavailable",
          message: "O serviço está indisponível. Tente em alguns segundos.",
        },
      },
      { status: 503 },
    );
  }

  const responseHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) responseHeaders.set(key, value);
  });

  if (isStream) {
    // Desativa buffering em qualquer proxy no caminho. Sem isto o
    // streaming desaparece silenciosamente (§7.1).
    responseHeaders.set("Cache-Control", "no-cache, no-transform");
    responseHeaders.set("X-Accel-Buffering", "no");
    responseHeaders.set("Connection", "keep-alive");
  }

  // upstream.body é repassado como stream, sem ser materializado.
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

type Ctx = { params: Promise<{ path: string[] }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  return proxy(req, (await ctx.params).path);
}
export async function POST(req: NextRequest, ctx: Ctx) {
  return proxy(req, (await ctx.params).path);
}
export async function PATCH(req: NextRequest, ctx: Ctx) {
  return proxy(req, (await ctx.params).path);
}
export async function PUT(req: NextRequest, ctx: Ctx) {
  return proxy(req, (await ctx.params).path);
}
export async function DELETE(req: NextRequest, ctx: Ctx) {
  return proxy(req, (await ctx.params).path);
}
