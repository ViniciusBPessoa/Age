/**
 * Logout — avisa o core-api e apaga os dois cookies.
 *
 * A ordem importa pouco aqui, mas a tolerância importa: se o core-api
 * estiver fora, os cookies são apagados de qualquer forma. Um logout
 * que falha porque o backend caiu deixa a pessoa presa numa sessão que
 * ela pediu para encerrar — e o refresh, que é o que vale 30 dias,
 * segue no browser.
 *
 * O caso contrário (cookie apagado, sessão ainda viva no banco) é bem
 * menos ruim: a sessão aparece na lista de dispositivos e pode ser
 * revogada de lá.
 */

import { type NextRequest } from "next/server";

import { ACCESS_COOKIE, CORE_API_URL, REFRESH_COOKIE } from "@/lib/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const EXPIRED = "Path=/; Max-Age=0; HttpOnly; SameSite=Lax";

export async function POST(req: NextRequest): Promise<Response> {
  const refresh = req.cookies.get(REFRESH_COOKIE)?.value;
  const access = req.cookies.get(ACCESS_COOKIE)?.value;

  if (refresh) {
    try {
      await fetch(new URL("/api/v1/auth/logout", CORE_API_URL), {
        method: "POST",
        headers: {
          Cookie: `${REFRESH_COOKIE}=${refresh}`,
          ...(access ? { Authorization: `Bearer ${access}` } : {}),
        },
        cache: "no-store",
      });
    } catch {
      // Ver o cabeçalho: falha aqui não impede apagar os cookies.
    }
  }

  const headers = new Headers({ "Content-Type": "application/json" });
  headers.append("Set-Cookie", `${ACCESS_COOKIE}=; ${EXPIRED}`);
  headers.append("Set-Cookie", `${REFRESH_COOKIE}=; ${EXPIRED}`);

  return new Response(JSON.stringify({ ok: true }), { status: 200, headers });
}
