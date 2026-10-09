/**
 * O mapa de nomes de tool não pode divergir do worker em silêncio.
 *
 * `toolLabel()` traduz `find_free_slots` em "procurando horários
 * livres" (§8.1). Quando uma tool nova é registrada no worker, o
 * fallback cobre — a tela mostra "consultando seus dados" em vez do
 * nome cru. Isso é degradação aceitável, não falha.
 *
 * O que NÃO é aceitável, e é o que este teste pega: uma entrada do mapa
 * apontando para uma tool que **não existe**. Foi exatamente o que
 * aconteceu ao escrever o mapa de cabeça — ele tinha `get_workload`,
 * `propose_events`, `move_event`, `delete_event`, `set_flexibility`,
 * `schedule_task`, `pause_routine` e `get_pending_sessions`, e nenhum
 * desses é o nome real. Oito entradas mortas, e nenhuma delas jamais
 * apareceria na tela — o texto bonito ficaria no arquivo e a UI
 * mostraria o genérico para sempre.
 *
 * Lê os nomes do fonte Python em vez de pedir ao worker de pé: o
 * `npm test` precisa rodar num clone novo, sem container.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { toolLabel } from "@/lib/sse";

const TOOLS_DIR = join(__dirname, "..", "..", "ai-worker", "app", "domain", "tools");

function workerToolNames(): Set<string> {
  const names = new Set<string>();
  for (const file of readdirSync(TOOLS_DIR)) {
    if (!file.endsWith("_tools.py")) continue;
    const source = readFileSync(join(TOOLS_DIR, file), "utf8");
    for (const match of source.matchAll(/name="([a-z_]+)"/g)) {
      names.add(match[1]);
    }
  }
  return names;
}

/** Relê o mapa do fonte: `TOOL_LABELS` não é exportado de propósito. */
function labelledNames(): string[] {
  const source = readFileSync(join(__dirname, "..", "lib", "sse.ts"), "utf8");
  const block = source.slice(
    source.indexOf("const TOOL_LABELS"),
    source.indexOf("export function toolLabel"),
  );
  return [...block.matchAll(/^\s{2}([a-z_]+):/gm)].map((m) => m[1]);
}

describe("rótulos de tool", () => {
  it("encontra as ferramentas do worker", () => {
    // Sanidade: se a extração quebrar, o resto passaria por vacuidade.
    expect(workerToolNames().size).toBeGreaterThan(20);
  });

  it("não tem entrada para tool que não existe no worker", () => {
    const real = workerToolNames();
    const dead = labelledNames().filter((name) => !real.has(name));
    expect(dead).toEqual([]);
  });

  it("cobre todas as tools registradas", () => {
    // Não é obrigatório cobrir tudo para a UI funcionar — o fallback
    // existe. Mas uma tool sem rótulo mostra "consultando seus dados",
    // e §8.1 quer que a linha diga o que está sendo feito. Então a
    // falta é um aviso legítimo, e não um detalhe.
    const labelled = new Set(labelledNames());
    const missing = [...workerToolNames()].filter((n) => !labelled.has(n));
    expect(missing).toEqual([]);
  });

  it("nunca devolve o nome cru da função", () => {
    // O pior resultado possível é a tela mostrar `reorder_task_list`:
    // expõe a implementação e ainda não informa.
    expect(toolLabel("uma_tool_que_nao_existe")).toBe("consultando seus dados");
    expect(toolLabel("find_free_slots")).toBe("procurando horários livres");
  });
});
