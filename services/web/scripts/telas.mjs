/**
 * Captura as telas do Age, logado, nos dois temas.
 *
 * Existe porque interface se julga VENDO, e a primeira versao deste
 * projeto passava em 99 testes sendo ruim de usar. Numa unica noite,
 * olhar a tela derrubou tres diagnosticos que eu tinha tirado lendo o
 * codigo e achou um defeito que nenhuma leitura pegaria: o anel de
 * foco de todo campo saia no azul padrao do Tailwind, nao na cor da
 * marca, porque 85 utilitarios de opacidade eram descartados em
 * silencio.
 *
 * Uso:
 *   node scripts/telas.mjs                      # em cima da :3000
 *   SAIDA=/tmp/antes node scripts/telas.mjs     # para comparar depois
 *
 * Prefira rodar contra PRODUCAO (`.\age.ps1 prod`) quando o assunto
 * for aparencia: em `next dev` o selo de dev do Next fica por cima do
 * rodape, e ja me fez caçar um fantasma por dez minutos.
 */

import { mkdirSync } from "node:fs";

import { chromium } from "playwright";

const BASE = process.env.BASE ?? "http://localhost:3000";
const SAIDA = process.env.SAIDA ?? "telas";
const EMAIL = process.env.AGE_EMAIL ?? "vinicius@age.dev.br";
const SENHA = process.env.AGE_SENHA ?? "age-dev-123456";

const ROTAS = [
  ["chat", "/chat"],
  ["agenda", "/calendar"],
  ["tarefas", "/tasks"],
  ["estudo", "/study"],
  ["rotinas", "/routines"],
  ["config", "/settings"],
];

const problemas = [];

async function entrar(page, tema) {
  /*
   * O tema vai pelo localStorage ANTES de qualquer pagina carregar,
   * que e exatamente como o app le (o ThemeToggle). Escrever
   * `dataset.theme` depois do carregamento tambem pinta a tela, mas
   * faz o React reclamar de hidratacao, e eu perdi tempo achando que
   * tinha achado um bug do app quando o bug era deste script.
   */
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("age-theme", t);
    } catch {
      /* navegacao privada pode recusar */
    }
  }, tema);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[type="email"]', EMAIL);
  await page.fill('input[type="password"]', SENHA);
  await page.click('button[type="submit"]');
  await page.waitForURL((u) => !u.pathname.includes("login"), {
    timeout: 20_000,
  });
}

async function capturar(navegador, tema, largura, sufixo) {
  const ctx = await navegador.newContext({
    viewport: { width: largura, height: 900 },
    isMobile: largura < 500,
    hasTouch: largura < 500,
  });
  const page = await ctx.newPage();
  page.on("pageerror", (e) =>
    problemas.push(`${tema}${sufixo}: ${String(e).slice(0, 120)}`),
  );
  page.on("console", (m) => {
    if (m.type() === "error") {
      problemas.push(`${tema}${sufixo}: ${m.text().slice(0, 120)}`);
    }
  });

  await entrar(page, tema);
  for (const [nome, rota] of ROTAS) {
    await page.goto(`${BASE}${rota}`, { waitUntil: "networkidle" });
    // Espera o conteudo assentar: sem isto a captura pega esqueleto.
    await page.waitForTimeout(1600);
    await page.screenshot({ path: `${SAIDA}/${tema}${sufixo}-${nome}.png` });
  }
  await ctx.close();
}

mkdirSync(SAIDA, { recursive: true });
const navegador = await chromium.launch();
for (const tema of ["dark", "light"]) {
  await capturar(navegador, tema, 1440, "");
}
await capturar(navegador, "dark", 390, "-celular");
await navegador.close();

console.log(`telas em ${SAIDA}/`);
console.log(
  problemas.length
    ? `PROBLEMAS DE CONSOLE:\n  ${problemas.slice(0, 10).join("\n  ")}`
    : "nenhum erro de console",
);
