/**
 * Renova a sessão ANTES de desenhar a página.
 *
 * O defeito: o cookie de acesso dura 15 minutos (§4.1.1) e o de
 * renovação, 30 dias. A renovação acontecia só no repasse de `/api/*`
 * (`app/api/[...path]/route.ts`). Num carregamento de PÁGINA (abrir o
 * Age no dia seguinte, apertar F5 depois do almoço), o layout das telas
 * via o acesso ausente e mandava para o login, com a sessão de 30 dias
 * ainda válida. Na prática, a pessoa entrava de novo a cada 15 minutos
 * parada.
 *
 * Aqui: sem acesso e com renovação, troca a renovação por um par novo
 * no core-api, entrega o acesso novo à própria requisição (para os
 * Server Components o lerem em `cookies()`) e grava os dois cookies na
 * resposta. Se a renovação não valer mais, segue sem mexer: o layout
 * manda para o login, como antes.
 */

import { NextResponse, type NextRequest } from "next/server";

import { ipDoCliente, proxiesConfiaveis } from "@/lib/bff-seguranca";

const ACCESS_COOKIE = "age_access";
const REFRESH_COOKIE = "age_refresh";
const CORE_API_URL = process.env.CORE_API_URL ?? "http://core-api:8000";

export const config = {
  runtime: "nodejs",
  // Páginas só. O `/api/*` já renova no próprio repasse; estático e
  // ícone não precisam de sessão.
  matcher: ["/((?!api/|_next/|icon\\.svg|favicon\\.ico|status).*)"],
};

async function renovar(
  refresh: string,
  ip: string | null,
): Promise<{ access: string; expiresIn: number; setCookie: string | null } | null> {
  for (let tentativa = 0; tentativa < 2; tentativa++) {
    let res: Response;
    try {
      res = await fetch(new URL("/api/v1/auth/refresh", CORE_API_URL), {
        method: "POST",
        headers: {
          Cookie: `${REFRESH_COOKIE}=${refresh}`,
          ...(ip ? { "X-Forwarded-For": ip } : {}),
        },
        cache: "no-store",
      });
    } catch {
      return null;
    }
    // 409: outra aba está renovando com o mesmo token neste instante.
    // Esperar e repetir recebe o par vigente (§4.1.2), em vez de
    // derrubar a sessão por uma corrida entre abas.
    if (res.status === 409 && tentativa === 0) {
      await new Promise((r) => setTimeout(r, 350));
      continue;
    }
    if (!res.ok) return null;
    const corpo = (await res.json()) as { access_token: string; expires_in: number };
    return {
      access: corpo.access_token,
      expiresIn: corpo.expires_in,
      setCookie: res.headers.get("set-cookie"),
    };
  }
  return null;
}

export async function middleware(req: NextRequest) {
  const access = req.cookies.get(ACCESS_COOKIE)?.value;
  const refresh = req.cookies.get(REFRESH_COOKIE)?.value;
  if (access || !refresh) return NextResponse.next();

  const ip = ipDoCliente(req.headers.get("x-forwarded-for"), proxiesConfiaveis());
  const novo = await renovar(refresh, ip);
  if (!novo) return NextResponse.next();

  // A requisição segue com o acesso novo, como se o cookie tivesse vindo
  // do navegador: é o que `cookies()` dos Server Components vai ler.
  const cabecalhos = new Headers(req.headers);
  const outros = (req.headers.get("cookie") ?? "")
    .split(";")
    .map((c) => c.trim())
    .filter((c) => c && !c.startsWith(`${ACCESS_COOKIE}=`));
  cabecalhos.set("cookie", [...outros, `${ACCESS_COOKIE}=${novo.access}`].join("; "));

  const resposta = NextResponse.next({ request: { headers: cabecalhos } });
  resposta.cookies.set(ACCESS_COOKIE, novo.access, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: Math.max(novo.expiresIn - 30, 30),
  });
  // O refresh ROTACIONADO vem do core-api e precisa chegar ao navegador,
  // senão a próxima renovação usa um token já consumido e a detecção de
  // reuso derruba a sessão legítima (§4.1.2).
  if (novo.setCookie) resposta.headers.append("Set-Cookie", novo.setCookie);
  return resposta;
}
