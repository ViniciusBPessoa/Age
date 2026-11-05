/**
 * A sessão de 30 dias vale também ao carregar uma PÁGINA.
 *
 * Antes do `middleware.ts`, a renovação só acontecia no repasse de
 * `/api/*`. Abrir o Age depois de 15 minutos parado mandava para o
 * login com a renovação ainda válida. Reproduzido com `curl` contra o
 * sistema no ar: 307 para /login; depois da correção, 200.
 */

import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { middleware } from "@/middleware";

function pedido(cookie: string): NextRequest {
  return new NextRequest("http://localhost:3000/calendar", {
    headers: { cookie, "x-forwarded-for": "203.0.113.7" },
  });
}

afterEach(() => vi.restoreAllMocks());

describe("middleware de sessão", () => {
  it("sem acesso e com renovação: renova e entrega o acesso à página", async () => {
    const fetchMock = vi.fn(async () =>
      Response.json(
        { access_token: "novo-acesso", expires_in: 900 },
        { headers: { "set-cookie": "age_refresh=rotacionado; Path=/; HttpOnly" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await middleware(pedido("age_refresh=velho"));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0] as unknown as [URL, RequestInit];
    expect((init.headers as Record<string, string>).Cookie).toBe("age_refresh=velho");
    // O IP que segue é o do socket, validado, não texto livre.
    expect((init.headers as Record<string, string>)["X-Forwarded-For"]).toBe(
      "203.0.113.7",
    );

    const setCookie = res.headers.getSetCookie().join("\n");
    expect(setCookie).toContain("age_access=novo-acesso");
    expect(setCookie).toContain("age_refresh=rotacionado");
    // A própria requisição segue com o acesso novo (é o que `cookies()`
    // dos Server Components lê).
    expect(res.headers.get("x-middleware-request-cookie")).toContain(
      "age_access=novo-acesso",
    );
  });

  it("com acesso válido não chama o core-api", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await middleware(pedido("age_access=ok; age_refresh=velho"));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("renovação recusada não grava cookie (o layout manda para o login)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 401 })),
    );
    const res = await middleware(pedido("age_refresh=revogado"));
    expect(res.headers.getSetCookie()).toEqual([]);
  });

  it("409 de outra aba renovando: espera e tenta de novo, sem derrubar", async () => {
    const respostas = [
      new Response("{}", { status: 409 }),
      Response.json({ access_token: "par-vigente", expires_in: 900 }),
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => respostas.shift()!),
    );
    const res = await middleware(pedido("age_refresh=velho"));
    expect(res.headers.getSetCookie().join("\n")).toContain("age_access=par-vigente");
  });
});
