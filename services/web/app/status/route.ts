/**
 * Status agregado dos serviços, para a página de diagnóstico do M0.
 *
 * Fica fora do BFF (/api/*) porque /readyz do core-api não é versionado
 * sob /api/v1 — ele mora na raiz, onde o Compose e o orquestrador
 * esperam encontrá-lo.
 */

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const CORE_API_URL = process.env.CORE_API_URL ?? "http://core-api:8000";

export async function GET() {
  try {
    const res = await fetch(new URL("/readyz", CORE_API_URL), {
      cache: "no-store",
    });
    return Response.json(await res.json(), { status: res.status });
  } catch {
    return Response.json(
      { ready: false, checks: { "core-api": "unreachable" } },
      { status: 503 },
    );
  }
}
