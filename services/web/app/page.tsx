/**
 * A raiz só decide para onde ir.
 *
 * Não existe landing page: o Age é ferramenta de uso diário, e quem
 * abre o endereço já quer a própria agenda. Mostrar uma página de
 * apresentação para quem já tem conta é um clique a mais em cima do
 * caminho mais percorrido do produto.
 *
 * A decisão olha só o cookie, não valida o token: validar aqui
 * significaria uma chamada ao core-api para toda abertura da raiz, e o
 * layout de `(app)` já valida de verdade — com o detalhe de que ele
 * pode renovar via cliente, o que esta rota não pode (ver
 * `lib/api-server.ts`).
 */

import { redirect } from "next/navigation";

import { hasSessionCookie } from "@/lib/api-server";

export const dynamic = "force-dynamic";

export default async function RootPage() {
  redirect((await hasSessionCookie()) ? "/chat" : "/login");
}
