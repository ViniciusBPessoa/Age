/**
 * O repasse das rotas que carregam credencial.
 *
 * `login` e `register` fazem a mesma coisa — chamam o core-api,
 * interceptam o access token do corpo e o guardam em cookie httpOnly
 * (§2.4.1). Estava escrito duas vezes; a diferença era o caminho.
 *
 * Extraído porque duplicar manuseio de credencial é como se chega a uma
 * das duas cópias ganhando um `Secure` que a outra não tem.
 */

import { type NextRequest } from "next/server";

import { ACCESS_COOKIE, CORE_API_URL, accessCookieOptions } from "@/lib/session";
import type { TokenResponse } from "@/lib/session";

const UPSTREAM_DOWN = {
  error: {
    code: "upstream_unavailable",
    message: "O serviço está indisponível. Tente em alguns segundos.",
  },
};

/** Monta o `Set-Cookie` do access a partir da resposta do core-api. */
export function accessSetCookie(token: TokenResponse): string {
  const opts = accessCookieOptions(token.expires_in);
  return [
    `${ACCESS_COOKIE}=${token.access_token}`,
    `Path=${opts.path}`,
    `Max-Age=${opts.maxAge}`,
    "HttpOnly",
    "SameSite=Lax",
    opts.secure ? "Secure" : "",
  ]
    .filter(Boolean)
    .join("; ");
}

/**
 * Chama uma rota de auth do core-api e converte a resposta numa que não
 * carrega credencial no corpo.
 *
 * `User-Agent` e `X-Forwarded-For` são repassados porque o core-api os
 * usa para rotular o dispositivo na lista de sessões (§4.1.1) — sem
 * isso toda sessão apareceria como vinda do próprio Next.
 */
export async function proxyTokenRoute(
  req: NextRequest,
  path: string,
): Promise<Response> {
  let upstream: Response;
  try {
    upstream = await fetch(new URL(`/api/v1/${path}`, CORE_API_URL), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-trace-id": req.headers.get("x-trace-id") ?? crypto.randomUUID(),
        "User-Agent": req.headers.get("user-agent") ?? "",
        "X-Forwarded-For": req.headers.get("x-forwarded-for") ?? "",
      },
      body: await req.arrayBuffer(),
      cache: "no-store",
    });
  } catch {
    return Response.json(UPSTREAM_DOWN, { status: 503 });
  }

  if (!upstream.ok) {
    // Erro volta como veio: a mensagem do core-api já é em pt-BR, e no
    // caso de credencial inválida já é deliberadamente vaga sobre o que
    // falhou (e-mail ou senha).
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { "Content-Type": "application/json" },
    });
  }

  const token = (await upstream.json()) as TokenResponse;

  const headers = new Headers({ "Content-Type": "application/json" });
  const refreshCookie = upstream.headers.get("set-cookie");
  if (refreshCookie) headers.append("Set-Cookie", refreshCookie);
  headers.append("Set-Cookie", accessSetCookie(token));

  return new Response(JSON.stringify({ ok: true }), { status: 200, headers });
}
