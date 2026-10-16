/**
 * BFF — anexa a credencial, repassa ao core-api, devolve.
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
 *
 * ## A parte que não é repasse puro: 401 e renovação
 *
 * §4.1.2 descreve a renovação acontecendo **no servidor do Next**,
 * invisível ao browser:
 *
 *   recebe 401 do core-api
 *     -> POST /auth/refresh com o refresh do cookie
 *     -> reescreve o cookie na resposta
 *     -> repete a chamada original UMA vez
 *
 * "Uma vez" é literal e importa: se o retry também der 401, a sessão
 * acabou de verdade e insistir viraria laço. O 401 sobe para o cliente,
 * que manda a pessoa para o login.
 *
 * Isto não é regra de negócio morando no BFF — é o mecanismo de
 * credencial que §4.1.1 atribui a esta camada. A fronteira que §2.4.1
 * protege é outra: nada de agendamento, de banco, de provedor.
 *
 * ## Por que o retry não vale para request com corpo
 *
 * `req.body` é um ReadableStream, e stream se lê uma vez. Para poder
 * repetir um POST seria preciso bufferizar o corpo inteiro na memória
 * do Next — o que quebraria o streaming de upload e daria um limite de
 * tamanho implícito. Em vez disso, o corpo é lido como ArrayBuffer só
 * para requests não-stream, cujo payload é JSON pequeno nesta API. O
 * SSE é GET, então nunca cai neste caso.
 */

import { type NextRequest } from "next/server";

import {
  ACCESS_COOKIE,
  CORE_API_URL,
  REFRESH_COOKIE,
  accessCookieOptions,
  refreshAccessToken,
} from "@/lib/session";

// Node, não edge: stream longo tem comportamento mais previsível aqui.
export const runtime = "nodejs";
// Sem isto, o Next tenta cachear e o stream quebra.
export const dynamic = "force-dynamic";

/** Cabeçalhos que não devem atravessar o proxy. */
const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "transfer-encoding",
  "upgrade",
  "host",
  "content-length",
  // O cookie não atravessa: quem autentica no core-api é o Bearer que
  // montamos aqui. Repassar o cookie de access seria dar ao core-api
  // uma segunda forma de autenticar, e duas formas é uma a mais do que
  // §4.1.1 define.
  "cookie",
  // Nunca deixar o cliente escolher a própria credencial.
  "authorization",
]);

/**
 * O trace da requisição: o que o cliente mandou, ou um novo.
 *
 * Calculado FORA de `forwardHeaders` porque o valor é necessário
 * também nas respostas que o BFF produz sozinho — o 503 de core-api
 * inacessível, que é justamente a falha em que alguém vai querer um id
 * para procurar no log e não vai encontrar nenhuma linha do core-api.
 */
function traceOf(req: NextRequest): string {
  return req.headers.get("x-trace-id") ?? crypto.randomUUID();
}

function forwardHeaders(
  req: NextRequest,
  accessToken: string | null,
  trace: string,
): Headers {
  const headers = new Headers();
  req.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) headers.set(key, value);
  });
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  // Rastreabilidade ponta a ponta: o trace_id nasce aqui e é propagado
  // até o worker, inclusive dentro do payload da fila (§12).
  headers.set("x-trace-id", trace);
  return headers;
}

const UPSTREAM_DOWN = {
  error: {
    code: "upstream_unavailable",
    message: "O serviço está indisponível. Tente em alguns segundos.",
  },
};

/** 503 com o trace no header, para a falha ficar localizável. */
function upstreamDown(trace: string): Response {
  return Response.json(UPSTREAM_DOWN, {
    status: 503,
    headers: { "x-trace-id": trace },
  });
}

/**
 * Rotas de auth que o BFF trata de forma especial, porque a resposta
 * carrega o access token no corpo e ele não pode chegar ao browser.
 * Ficam em `app/api/auth/*`, que o roteador do Next casa antes deste
 * catch-all — a lista aqui é a trava para o caso de alguém chamar
 * `/api/v1/...` por outro caminho.
 */
const TOKEN_BEARING = new Set(["auth/login", "auth/register", "auth/refresh"]);

async function proxy(req: NextRequest, path: string[]): Promise<Response> {
  const joined = path.join("/");

  if (TOKEN_BEARING.has(joined)) {
    return Response.json(
      {
        error: {
          code: "use_bff_auth_route",
          message: "Esta rota devolve credencial e precisa passar por /api/auth/*.",
        },
      },
      { status: 400 },
    );
  }

  const target = new URL(`/api/v1/${joined}`, CORE_API_URL);
  target.search = req.nextUrl.search;

  const isStream = path.includes("stream");
  const jar = req.cookies;
  const accessToken = jar.get(ACCESS_COOKIE)?.value ?? null;

  // Corpo bufferizado para poder repetir após renovar (ver cabeçalho).
  const hasBody = req.method !== "GET" && req.method !== "HEAD";
  const body = hasBody ? await req.arrayBuffer() : undefined;

  const trace = traceOf(req);
  const send = (token: string | null) =>
    fetch(target, {
      method: req.method,
      headers: forwardHeaders(req, token, trace),
      body,
      cache: "no-store",
      redirect: "manual",
    });

  let upstream: Response;
  try {
    upstream = await send(accessToken);
  } catch {
    return upstreamDown(trace);
  }

  // §4.1.2: renova e repete uma vez. O stream também passa por aqui —
  // o 401 chega no GET que abre o stream, antes de qualquer byte de
  // evento, então repetir é seguro.
  let renewed: { setCookie: string | null; expiresIn: number; value: string } | null =
    null;

  if (upstream.status === 401) {
    const refreshToken = jar.get(REFRESH_COOKIE)?.value;
    if (refreshToken) {
      const result = await refreshAccessToken(refreshToken);
      if (result) {
        renewed = {
          setCookie: result.setCookie,
          expiresIn: result.token.expires_in,
          value: result.token.access_token,
        };
        try {
          upstream = await send(result.token.access_token);
        } catch {
          return upstreamDown(trace);
        }
      }
    }
  }

  const responseHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) responseHeaders.set(key, value);
  });

  if (renewed) {
    // O refresh rotacionado vem do core-api; repassar é obrigatório,
    // senão a próxima renovação usa um token já consumido e a detecção
    // de reuso derruba a sessão legítima (§4.1.2).
    if (renewed.setCookie) {
      responseHeaders.append("Set-Cookie", renewed.setCookie);
    }
    const opts = accessCookieOptions(renewed.expiresIn);
    responseHeaders.append(
      "Set-Cookie",
      [
        `${ACCESS_COOKIE}=${renewed.value}`,
        `Path=${opts.path}`,
        `Max-Age=${opts.maxAge}`,
        "HttpOnly",
        `SameSite=Lax`,
        opts.secure ? "Secure" : "",
      ]
        .filter(Boolean)
        .join("; "),
    );
  }

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
