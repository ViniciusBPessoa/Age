import { resolve } from "node:path";

import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    // Mesmo `@/*` do tsconfig: sem isto o Vitest não resolve os imports
    // que o Next resolve, e o teste falha por motivo não relacionado ao
    // que ele testa.
    alias: { "@": resolve(__dirname, ".") },
  },
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
  },
});
