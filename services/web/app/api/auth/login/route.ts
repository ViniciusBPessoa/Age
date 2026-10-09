/**
 * Login — a rota em que o BFF não pode ser repasse puro.
 *
 * `POST /auth/login` do core-api devolve `{access_token, expires_in}` no
 * corpo. Repassar esse corpo colocaria o token no JavaScript da página,
 * que é precisamente o que §2.4.1 diz que o BFF existe para evitar.
 *
 * O token é interceptado em `proxyTokenRoute`, guardado em cookie
 * httpOnly, e o browser recebe só `{ok: true}`.
 *
 * Nada de regra de negócio: autenticar continua sendo trabalho do
 * core-api, que valida a senha, cria a sessão e detecta reuso de
 * refresh. Aqui só se decide onde a credencial mora.
 */

import { type NextRequest } from "next/server";

import { proxyTokenRoute } from "@/lib/auth-proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest): Promise<Response> {
  return proxyTokenRoute(req, "auth/login");
}
