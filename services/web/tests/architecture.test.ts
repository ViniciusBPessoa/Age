/**
 * As invariantes de arquitetura do front.
 *
 * `core-api/tests/test_architecture.py` já cobre o `package.json`
 * (§2.4.1: nada de `pg`, `prisma`, SDK de LLM). O que falta é o que só
 * se vê lendo os arquivos do `web`, e são duas regras que o documento
 * trata como estruturais:
 *
 *   1. **§2.4**: a biblioteca de calendário fica dentro de
 *      `<CalendarTimeline>`. "Mesmo princípio das portas do backend,
 *      aplicado na UI" — e porta sem teste é convenção.
 *
 *   2. **§2.4.1**: o BFF só anexa credencial e repassa. Nenhuma regra
 *      de agendamento, nenhum acesso a banco, nenhuma chamada a
 *      provedor.
 *
 * A primeira importa porque §2.4 registra que a escolha da lib pode ser
 * reaberta ("se o `eventPropGetter` não der conta, FullCalendar tem
 * controle mais fino"). Reabrir é barato só enquanto o import estiver
 * num lugar; a partir do segundo arquivo que importa
 * `react-big-calendar`, trocar a lib deixa de ser trocar um componente.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";

import { describe, expect, it } from "vitest";

const WEB_ROOT = join(__dirname, "..");

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry === ".next" || entry === "tests") {
      continue;
    }
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...sourceFiles(full));
    else if (/\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

const FILES = sourceFiles(WEB_ROOT);

describe("§2.4 — a lib de calendário fica encapsulada", () => {
  it("encontra os fontes do web", () => {
    expect(FILES.length).toBeGreaterThan(20);
  });

  it("só `components/calendar-timeline/` importa react-big-calendar", () => {
    const offenders = FILES.filter((file) => {
      const source = readFileSync(file, "utf8");
      if (!/from\s+["']react-big-calendar/.test(source)) return false;
      return !relative(WEB_ROOT, file).startsWith(
        join("components", "calendar-timeline"),
      );
    }).map((file) => relative(WEB_ROOT, file));

    expect(
      offenders,
      "trocar a lib de calendário (§2.4 deixa isso em aberto) tem de ser " +
        "trocar UM componente. Use as props de domínio de <CalendarTimeline>.",
    ).toEqual([]);
  });
});

describe("§2.4.1 — o BFF só repassa", () => {
  const bffFiles = FILES.filter((file) =>
    relative(WEB_ROOT, file).startsWith(join("app", "api")),
  );

  it("encontra as rotas de BFF", () => {
    expect(bffFiles.length).toBeGreaterThan(0);
  });

  /**
   * O que caracteriza regra de negócio vazando para cá.
   *
   * A lista é de sintomas, não de palavras proibidas: `find_free_slots`
   * numa rota do Next significaria busca de horário acontecendo fora do
   * `Scheduler` (§5.3), e `date-fns` significaria aritmética de tempo
   * fora do backend (§9.5).
   */
  const FORBIDDEN: [pattern: RegExp, why: string][] = [
    [
      /from\s+["']date-fns/,
      "aritmética de data no BFF — o cálculo é do backend (§9.5)",
    ],
    [/from\s+["']@date-fns/, "aritmética de data no BFF (§9.5)"],
    [/\b(pg|postgres|prisma|drizzle)\b/, "acesso a banco no front (§2.4.1)"],
    [
      /from\s+["'](openai|@anthropic-ai|@google\/gener)/,
      "SDK de LLM no front (§2.4.1)",
    ],
    [
      /find_free_slots|findFreeSlots/,
      "busca de horário no BFF — é do Scheduler (§5.3)",
    ],
    [/validate_plan|validatePlan/, "validação de plano no BFF (§5.7)"],
  ];

  for (const [pattern, why] of FORBIDDEN) {
    it(`nenhuma rota de BFF tem: ${why}`, () => {
      const offenders = bffFiles
        .filter((file) => pattern.test(readFileSync(file, "utf8")))
        .map((file) => relative(WEB_ROOT, file));
      expect(offenders).toEqual([]);
    });
  }
});

describe("§4.1.1 — a credencial não chega ao JavaScript da página", () => {
  /**
   * O access token mora em cookie httpOnly, e o browser nunca o vê.
   *
   * A armadilha concreta: `POST /auth/login` do core-api devolve
   * `access_token` **no corpo**. Um BFF de repasse puro entregaria esse
   * corpo ao browser — e aí o token estaria no JS da página, que é
   * exatamente o que §2.4.1 diz que o BFF existe para evitar.
   *
   * Este teste cobra que nenhum Client Component leia `access_token`.
   */
  it("nenhum componente de cliente lê access_token", () => {
    const offenders = FILES.filter((file) => {
      const source = readFileSync(file, "utf8");
      if (!source.includes('"use client"')) return false;
      return /access_token|age_access/.test(source);
    }).map((file) => relative(WEB_ROOT, file));

    expect(
      offenders,
      "o token fica no servidor (§4.1.1). O cliente chama /api/* e o " +
        "cookie httpOnly viaja sozinho.",
    ).toEqual([]);
  });

  it("o catch-all do BFF não repassa o cookie nem o Authorization do cliente", () => {
    const route = readFileSync(
      join(WEB_ROOT, "app", "api", "[...path]", "route.ts"),
      "utf8",
    );
    // Deixar o cliente escolher o próprio `Authorization` daria a ele
    // uma segunda forma de autenticar no core-api, e duas é uma a mais
    // do que §4.1.1 define.
    expect(route).toMatch(/"cookie"/);
    expect(route).toMatch(/"authorization"/);
  });
});

describe("§8.2 — acessibilidade e idioma", () => {
  it("o streaming é anunciado com aria-live", () => {
    const messages = readFileSync(
      join(WEB_ROOT, "components", "chat", "messages.tsx"),
      "utf8",
    );
    // §8.2 pede `aria-live="polite"` no streaming por nome. Sem isso,
    // quem usa leitor de tela não recebe a resposta que está chegando.
    expect(messages).toContain('aria-live="polite"');
  });

  it("nenhum componente remove o foco sem substituto", () => {
    // `outline: none` sem anel de substituição é a falha de
    // acessibilidade mais comum em UI escura: quem navega por teclado
    // perde a noção de onde está.
    const offenders = FILES.filter((file) =>
      /outline-none/.test(readFileSync(file, "utf8")),
    )
      .filter((file) => {
        const source = readFileSync(file, "utf8");
        // Aceita quando há foco explícito logo depois (`focus:border-`
        // ou `focus-within:border-`), que é o anel desta UI.
        return !/focus(-within)?:border-/.test(source);
      })
      .map((file) => relative(WEB_ROOT, file));

    expect(offenders).toEqual([]);
  });

  it("a interface está em pt-BR", () => {
    const layout = readFileSync(join(WEB_ROOT, "app", "layout.tsx"), "utf8");
    expect(layout).toContain('lang="pt-BR"');
  });
});

describe("§9.5 — o fuso vem da conta, não do browser", () => {
  /**
   * Todo cálculo de tempo acontece no backend, e o front formata no
   * fuso de `users.timezone`.
   *
   * `Intl.DateTimeFormat().resolvedOptions().timeZone` lê o fuso do
   * browser. Ele é legítimo em UM lugar — o palpite inicial no cadastro
   * — e errado em qualquer outro: quem viaja veria a agenda inteira se
   * deslocar sem ter pedido.
   */
  it("só o cadastro usa o fuso do browser", () => {
    const offenders = FILES.filter((file) =>
      /resolvedOptions\(\)\s*\.\s*timeZone/.test(readFileSync(file, "utf8")),
    )
      .map((file) => relative(WEB_ROOT, file))
      .filter((path) => !path.includes(`register${sep}page.tsx`));

    expect(
      offenders,
      "use o `timezone` da conta (§9.5): o fuso do browser muda quando a " +
        "pessoa viaja, e a agenda dela não deveria.",
    ).toEqual([]);
  });
});
