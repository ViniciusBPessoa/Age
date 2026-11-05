/**
 * As defesas do BFF (`lib/bff-seguranca.ts`).
 *
 * Os três furos que a auditoria reproduziu com `curl` contra o sistema
 * no ar, cada um com o caso exato que passava antes:
 *
 *   /api/x/..%2Fauth%2Flogin            -> devolvia access_token no corpo
 *   X-Forwarded-For: 10.0.<n>.1         -> 25 logins sem nenhum 429
 *   todo header do cliente atravessava  -> inclusive x-age-user-id
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  CORPO_MAXIMO,
  cabecalhosParaRepasse,
  destinoSeguro,
  ipDoCliente,
  ipValido,
  lerCorpoLimitado,
  traceSeguro,
} from "@/lib/bff-seguranca";

const BASE = "http://core-api:8000";

describe("destinoSeguro: o caminho não sai de /api/v1/", () => {
  it("caminho normal vira /api/v1/...", () => {
    expect(destinoSeguro(["calendar", "events"], BASE)?.pathname).toBe(
      "/api/v1/calendar/events",
    );
  });

  it.each([
    [["x", "../auth/login"]], // o que o Next entrega para ..%2Fauth%2Flogin
    [["x", "..", "..", "openapi.json"]],
    [["calendar", "..%2F..", "healthz"].map(decodeURIComponent)],
    [[".", "auth", "login"]],
    [["auth", "", "login"]],
    [["a\\..\\b"]],
    [["internal", "calendar\u0000"]],
    [[]],
  ])("recusa %j", (path) => {
    expect(destinoSeguro(path as string[], BASE)).toBeNull();
  });

  it("caractere especial é codificado, não interpretado", () => {
    const url = destinoSeguro(["tasks", "a?b#c"], BASE);
    expect(url?.pathname).toBe("/api/v1/tasks/a%3Fb%23c");
    expect(url?.search).toBe("");
  });
});

describe("ipDoCliente: o IP vem da direita, não da esquerda", () => {
  it("sem proxy, vale a última entrada (o socket)", () => {
    // `scripts/ip-do-cliente.cjs` garante que a última é o socket.
    expect(ipDoCliente("6.6.6.6, 203.0.113.9", 0)).toBe("203.0.113.9");
  });

  it("com um proxy confiável, vale a penúltima", () => {
    expect(ipDoCliente("6.6.6.6, 198.51.100.7, 172.18.0.2", 1)).toBe("198.51.100.7");
  });

  it("a primeira entrada, que o atacante escreve, nunca é escolhida", () => {
    expect(ipDoCliente("1.2.3.4, 203.0.113.9", 0)).not.toBe("1.2.3.4");
  });

  it("lixo não vira IP", () => {
    expect(ipDoCliente("drop table, <script>", 0)).toBeNull();
    expect(ipValido("::ffff:10.0.0.1")).toBe("10.0.0.1");
    expect(ipValido("2001:db8::1")).toBe("2001:db8::1");
    expect(ipValido("999.1.1.1")).toBeNull();
  });
});

describe("cabecalhosParaRepasse: lista fechada", () => {
  it("só passa o que está na lista", () => {
    const origem = new Headers({
      "content-type": "application/json",
      "idempotency-key": "abc",
      cookie: "age_access=roubado",
      authorization: "Bearer forjado",
      "x-forwarded-for": "1.2.3.4",
      "x-age-user-id": "00000000-0000-0000-0000-000000000000",
      "x-trace-id": "qualquer",
    });
    const saida = cabecalhosParaRepasse(origem);
    expect(saida.get("content-type")).toBe("application/json");
    expect(saida.get("idempotency-key")).toBe("abc");
    for (const proibido of [
      "cookie",
      "authorization",
      "x-forwarded-for",
      "x-age-user-id",
      "x-trace-id",
    ]) {
      expect(saida.has(proibido), proibido).toBe(false);
    }
  });
});

describe("traceSeguro: id ou nada", () => {
  it("aceita id e troca texto livre por um novo", () => {
    expect(traceSeguro("abc123def456")).toBe("abc123def456");
    const trocado = traceSeguro('x\n{"level":"ERROR","msg":"forjado"}');
    expect(trocado).not.toContain("forjado");
    expect(trocado).toMatch(/^[0-9a-f-]{36}$/);
  });
});

describe("lerCorpoLimitado: corpo com teto", () => {
  it("corpo pequeno passa inteiro", async () => {
    const req = new Request("http://x", { method: "POST", body: "olá" });
    const corpo = await lerCorpoLimitado(req);
    expect(new TextDecoder().decode(corpo!)).toBe("olá");
  });

  it("Content-Length acima do teto é recusado sem ler", async () => {
    const req = new Request("http://x", {
      method: "POST",
      body: "x",
      headers: { "content-length": String(CORPO_MAXIMO + 1) },
    });
    expect(await lerCorpoLimitado(req)).toBeNull();
  });

  it("corpo que mente no tamanho é cortado na leitura", async () => {
    const grande = new Uint8Array(2048);
    const req = new Request("http://x", { method: "POST", body: grande });
    expect(await lerCorpoLimitado(req, 1024)).toBeNull();
  });
});

describe("o BFF usa as defesas (não basta existirem)", () => {
  const ROOT = join(__dirname, "..");
  const rota = readFileSync(join(ROOT, "app", "api", "[...path]", "route.ts"), "utf8");
  const login = readFileSync(join(ROOT, "lib", "auth-proxy.ts"), "utf8");

  it("o catch-all valida o caminho e filtra os headers", () => {
    expect(rota).toContain("destinoSeguro(");
    expect(rota).toContain("cabecalhosParaRepasse(");
    expect(rota).toContain("lerCorpoLimitado(");
    // A forma antiga, que deixava a travessia passar:
    expect(rota).not.toMatch(/new URL\(`\/api\/v1\/\$\{joined\}`/);
  });

  it("o login não repassa o X-Forwarded-For do navegador", () => {
    expect(login).not.toMatch(/req\.headers\.get\("x-forwarded-for"\) \?\? ""/);
    expect(login).toContain("ipDoCliente(");
  });

  it("o servidor sobe com o script que fixa o IP do socket", () => {
    const pkg = JSON.parse(readFileSync(join(ROOT, "package.json"), "utf8"));
    expect(pkg.scripts.start).toContain("ip-do-cliente.cjs");
    expect(pkg.scripts.dev).toContain("ip-do-cliente.cjs");
  });

  it("next.config manda os cabeçalhos de segurança", () => {
    const cfg = readFileSync(join(ROOT, "next.config.ts"), "utf8");
    for (const h of [
      "Content-Security-Policy",
      "X-Frame-Options",
      "X-Content-Type-Options",
      "Referrer-Policy",
      "poweredByHeader: false",
    ]) {
      expect(cfg, h).toContain(h);
    }
  });
});
