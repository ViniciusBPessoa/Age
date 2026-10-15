import { resolve } from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Mesmo `@/*` do tsconfig: sem isto o Vitest não resolve os imports que
// o Next resolve, e o teste falha por motivo não relacionado ao que ele
// testa.
const alias = { "@": resolve(__dirname, ".") };

export default defineConfig({
  test: {
    // Dois projetos, porque os dois tipos de teste pedem ambientes
    // diferentes — e `environmentMatchGlobs`, que fazia isso por glob,
    // está depreciado nesta versão do Vitest.
    //
    // A alternativa seria `jsdom` para tudo, e ela custa caro aqui:
    // `architecture.test.ts` e `tool-labels.test.ts` leem o disco com
    // `node:fs` e varrem os arquivos do worker, e `contract.test.ts`
    // compara tipos com o OpenAPI. Nenhum toca o DOM, e todos ficariam
    // mais lentos num ambiente simulado que não usam.
    projects: [
      {
        resolve: { alias },
        test: {
          name: "node",
          environment: "node",
          // Os de componente moram em `tests/components/` e são
          // excluídos aqui para não rodar duas vezes.
          include: ["tests/**/*.test.ts"],
          exclude: ["tests/components/**"],
        },
      },
      {
        plugins: [react()],
        resolve: { alias },
        test: {
          name: "components",
          environment: "jsdom",
          // `.tsx` precisa entrar: o padrão anterior era só `.test.ts`,
          // o que excluía silenciosamente qualquer teste de componente —
          // um arquivo novo não rodaria e ninguém veria falha.
          include: ["tests/components/**/*.test.{ts,tsx}"],
          setupFiles: ["./tests/setup-components.ts"],
        },
      },
    ],
  },
});
