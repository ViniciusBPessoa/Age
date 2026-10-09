/**
 * Registro — mesmo tratamento de credencial do login.
 *
 * O core-api já entra na conta ao registrar ("pedir login logo após
 * registrar é atrito sem ganho"), então esta rota também devolve token
 * no corpo e também precisa interceptá-lo (§2.4.1).
 */

import { type NextRequest } from "next/server";

import { proxyTokenRoute } from "@/lib/auth-proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest): Promise<Response> {
  return proxyTokenRoute(req, "auth/register");
}
