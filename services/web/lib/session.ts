/**
 * A sessão do lado do servidor do Next.
 *
 * ARQUITETURA.md §4.1.1 define três credenciais distintas, e diz que
 * confundi-las é "a falha de segurança mais provável desta
 * arquitetura". Este arquivo existe para que a fronteira entre as duas
 * primeiras seja código, e não disciplina:
 *
 *   browser → web       cookie httpOnly  (o JS da página nunca lê)
 *   web     → core-api  Authorization: Bearer <access JWT>
 *
 * O ponto que obriga este módulo a existir: `POST /auth/login` do
 * core-api devolve o access token **no corpo da resposta**. Um BFF de
 * repasse puro entregaria esse corpo ao browser, e aí o token estaria
 * no JavaScript da página — exatamente o que §2.4.1 diz que o BFF
 * existe para evitar. Então o login não pode ser repasse: a resposta é
 * interceptada aqui, o token vira cookie httpOnly, e o que chega ao
 * browser é só `{ ok: true }`.
 *
 * Dois cookies, não um, porque têm vidas diferentes (§4.1.1):
 *   age_access   15 min  — emitido aqui a partir do corpo da resposta
 *   age_refresh  30 dias — emitido pelo core-api, nós só repassamos
 *
 * O `age_refresh` é setado pelo próprio core-api via `Set-Cookie`. Como
 * o browser fala só com o `web`, esse cabeçalho atravessa o proxy e
 * chega ao browser como se fosse nosso — o domínio é o mesmo. Não
 * reescrevemos: o core-api é a autoridade sobre a vida do refresh.
 */

import { cookies } from "next/headers";

export const ACCESS_COOKIE = "age_access";
export const REFRESH_COOKIE = "age_refresh";

export const CORE_API_URL = process.env.CORE_API_URL ?? "http://core-api:8000";

/** `Secure` só em produção: em dev o host é http://localhost. */
const IS_PROD = process.env.NODE_ENV === "production";

export interface TokenResponse {
  access_token: string;
  expires_in: number;
  token_type?: string;
}

/**
 * Opções do cookie de access.
 *
 * `maxAge` é 30 s menor que o `expires_in` do token de propósito: um
 * cookie que expira *junto* com o token cria uma janela em que o
 * browser ainda manda um token que o core-api já recusa, e a tentativa
 * de renovação acontece com atraso de um request. Deixar o cookie
 * morrer antes faz a renovação começar cedo.
 */
export function accessCookieOptions(expiresIn: number) {
  return {
    httpOnly: true,
    secure: IS_PROD,
    sameSite: "lax" as const,
    path: "/",
    maxAge: Math.max(expiresIn - 30, 30),
  };
}

export async function readAccessToken(): Promise<string | null> {
  return (await cookies()).get(ACCESS_COOKIE)?.value ?? null;
}

export async function readRefreshToken(): Promise<string | null> {
  return (await cookies()).get(REFRESH_COOKIE)?.value ?? null;
}

/** Grava o access token recebido do core-api como cookie httpOnly. */
export async function writeAccessToken(token: TokenResponse): Promise<void> {
  (await cookies()).set(
    ACCESS_COOKIE,
    token.access_token,
    accessCookieOptions(token.expires_in),
  );
}

export async function clearSessionCookies(): Promise<void> {
  const jar = await cookies();
  jar.delete(ACCESS_COOKIE);
  jar.delete(REFRESH_COOKIE);
}

/**
 * Renova o access token usando o refresh do cookie (§4.1.2).
 *
 * Devolve o par novo e o `Set-Cookie` que o core-api emitiu para o
 * refresh rotacionado — quem chamou precisa repassar esse cabeçalho,
 * senão o refresh antigo fica no browser e a **próxima** renovação
 * dispara a detecção de reuso, que invalida a família inteira de tokens
 * e desloga a pessoa. É um bug que só aparece 15 minutos depois, o que
 * o torna especialmente chato de achar.
 *
 * A corrida de renovação concorrente é resolvida no core-api, com lock
 * no Redis por sessão (§4.1.2) — não aqui. O BFF do Next roda em
 * múltiplos processos e não tem estado compartilhado, então um lock em
 * memória daria falsa segurança.
 */
export async function refreshAccessToken(refreshToken: string): Promise<{
  token: TokenResponse;
  setCookie: string | null;
} | null> {
  let res: Response;
  try {
    res = await fetch(new URL("/api/v1/auth/refresh", CORE_API_URL), {
      method: "POST",
      headers: { Cookie: `${REFRESH_COOKIE}=${refreshToken}` },
      cache: "no-store",
    });
  } catch {
    return null;
  }

  if (!res.ok) return null;

  const token = (await res.json()) as TokenResponse;
  return { token, setCookie: res.headers.get("set-cookie") };
}
