/**
 * A fronteira TS↔Python, verificada contra o OpenAPI de verdade.
 *
 * §2.5 prevê `age-types` **gerado** por `make gen-types`, para que
 * "assinatura de endpoint não possa divergir entre back e front sem o
 * build quebrar". `lib/types.ts` é escrito à mão (a razão está no
 * cabeçalho dele), então a rede que o gerador daria precisa vir daqui.
 *
 * ## Por que este teste existe, concretamente
 *
 * Escrever os tipos de cabeça custou sete erros, e nenhum deles o
 * compilador podia pegar:
 *
 *   - `Credential.is_default`, `Credential.model` — não existem; os
 *     campos reais são `key_last4`, `default_model`, `is_active`;
 *   - `Preferences.timezone`, `session_default_min`,
 *     `daily_study_cap_min` — nenhum existe;
 *   - `PrivacyLimits.limits: string[]` — são três campos nomeados;
 *   - `Me.created_at` — não existe, e faltavam `locale`, `role`,
 *     `email_verified`;
 *   - `PendingSession` inteira, com nomes inventados.
 *
 * E mais três do lado dos parâmetros, que são piores porque o FastAPI
 * **ignora parâmetro desconhecido em silêncio** — a tela funciona, só
 * não faz o que diz:
 *
 *   - `GET /tasks?include_done=` (o nome real é `status`, por alias);
 *   - `GET /routines?include_paused=` (é `include_ended`);
 *   - `PATCH /routines/{id}` com `paused` no corpo (é query param).
 *
 * ## Como roda
 *
 * Precisa do core-api de pé. Sem ele, os testes são **pulados**, não
 * falhados: `npm test` num clone novo não deve quebrar por falta de
 * container. O smoke da UI roda isto com tudo no ar, e aí o skip não
 * acontece — é lá que o teste vale.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { beforeAll, describe, expect, it } from "vitest";

const CORE_API = process.env.CORE_API_URL ?? "http://localhost:8000";
/** A raiz do core-api no disco, para ler os enums direto da fonte. */
const BACKEND = join(__dirname, "..", "..", "core-api");

interface OpenApiSchema {
  properties?: Record<string, unknown>;
  required?: string[];
}

interface OpenApi {
  components: { schemas: Record<string, OpenApiSchema> };
  paths: Record<
    string,
    Record<string, { parameters?: { name: string; in: string }[] }>
  >;
}

let spec: OpenApi | null = null;

beforeAll(async () => {
  try {
    const res = await fetch(`${CORE_API}/openapi.json`, {
      signal: AbortSignal.timeout(5000),
    });
    if (res.ok) spec = (await res.json()) as OpenApi;
  } catch {
    spec = null;
  }
});

/** Os campos que `lib/types.ts` declara para uma interface. */
function declaredFields(name: string): string[] {
  const source = readFileSync(join(__dirname, "..", "lib", "types.ts"), "utf8");
  const start = source.indexOf(`export interface ${name} {`);
  if (start === -1) throw new Error(`interface ${name} não existe em types.ts`);
  const body = source.slice(start, source.indexOf("\n}", start));
  return [...body.matchAll(/^\s{2}(\w+)[?]?:/gm)].map((m) => m[1]);
}

/**
 * Cada par liga uma interface do front ao schema do OpenAPI.
 *
 * Só os que a UI realmente consome. Um schema sem interface
 * correspondente não é erro: nem todo `*Out` do backend aparece na
 * tela.
 */
const PAIRS: [tsInterface: string, openApiSchema: string][] = [
  ["Me", "MeResponse"],
  ["SessionInfo", "SessionInfo"],
  ["ProviderInfo", "ProviderInfo"],
  ["Credential", "CredentialOut"],
  ["VerifyResult", "VerifyResult"],
  ["CalendarRef", "CalendarOut"],
  ["AgeEvent", "EventOut"],
  ["Slot", "SlotOut"],
  ["FreeInterval", "FreeIntervalOut"],
  ["FreeTime", "FreeTimeOut"],
  ["TaskListRef", "TaskListOut"],
  ["Reserved", "ReservedOut"],
  ["Task", "TaskOut"],
  ["TaskOverview", "OverviewOut"],
  // FastAPI qualifica o nome quando dois módulos declaram o mesmo
  // schema. `ConflictOut` existe em `plans` (kind/message/severity) e em
  // `routines` (start/end/with_title/blocking) — são conflitos
  // diferentes, e o nome curto não identifica nenhum dos dois.
  ["Conflict", "app__api__plans__ConflictOut"],
  ["RoutineConflict", "app__api__routines__ConflictOut"],
  ["PlanItem", "PlanItemOut"],
  ["Plan", "PlanOut"],
  ["ApplyResult", "ApplyOut"],
  ["Conversation", "ConversationOut"],
  ["Message", "MessageOut"],
  ["Subject", "SubjectOut"],
  ["SubjectProgress", "ProgressOut"],
  ["Routine", "RoutineOut"],
  ["Occurrence", "OccurrenceOut"],
  ["Adherence", "AdherenceOut"],
  ["RoutinePreview", "PreviewOut"],
  ["Preferences", "PreferencesOut"],
  ["PrivacyLimits", "LimitsOut"],
  ["ExportCounts", "ExportCounts"],
  ["DeletionPreview", "DeletionPreviewOut"],
  ["PeriodReport", "ReportOut"],
  ["ExternalChange", "ExternalChangeOut"],
  ["ExternalChanges", "ExternalChangesOut"],
];

describe("tipos do front vs. OpenAPI do core-api", () => {
  it("o OpenAPI está acessível (senão os testes abaixo são pulados)", () => {
    if (!spec) {
      console.warn(`core-api fora do ar em ${CORE_API} — testes de contrato pulados`);
    }
    expect(true).toBe(true);
  });

  for (const [tsName, schemaName] of PAIRS) {
    it(`${tsName} não declara campo que ${schemaName} não tem`, () => {
      if (!spec) return;
      const schema = spec.components.schemas[schemaName];
      expect(schema, `schema ${schemaName} não existe no OpenAPI`).toBeDefined();

      const real = new Set(Object.keys(schema.properties ?? {}));
      const invented = declaredFields(tsName).filter((f) => !real.has(f));
      expect(invented).toEqual([]);
    });

    it(`${tsName} declara todos os campos obrigatórios de ${schemaName}`, () => {
      if (!spec) return;
      const schema = spec.components.schemas[schemaName];
      if (!schema) return;

      // Campo obrigatório que o front não conhece é dado perdido: a UI
      // nunca o mostra, e ninguém descobre.
      const declared = new Set(declaredFields(tsName));
      const missing = (schema.required ?? []).filter((f) => !declared.has(f));
      expect(missing).toEqual([]);
    });
  }
});

/**
 * Os parâmetros de query que a UI manda.
 *
 * Esta é a metade mais perigosa: o FastAPI descarta parâmetro
 * desconhecido sem erro, então `?include_done=true` numa rota que
 * espera `?status=` devolve 200 com o resultado errado. Três bugs desse
 * tipo saíram desta UI, e nenhum deles apareceu como falha — só como
 * tela que não obedecia o controle.
 */
const QUERY_USAGE: [method: string, path: string, params: string[]][] = [
  ["get", "/api/v1/tasks", ["status"]],
  ["get", "/api/v1/tasks/overview", ["horizon_days"]],
  ["get", "/api/v1/calendar/events", ["start", "end"]],
  [
    "get",
    "/api/v1/calendar/free-slots",
    ["start", "end", "duration_min", "deadline", "count"],
  ],
  ["get", "/api/v1/calendar/external-changes", ["acknowledge"]],
  ["get", "/api/v1/conversations", ["limit", "q"]],
  ["get", "/api/v1/conversations/{conversation_id}/messages", ["limit"]],
  ["get", "/api/v1/plans", ["status"]],
  ["get", "/api/v1/routines", ["include_ended"]],
  ["get", "/api/v1/routines/{routine_id}/occurrences", ["from", "to"]],
  ["get", "/api/v1/routines/{routine_id}/adherence", ["days"]],
  ["patch", "/api/v1/routines/{routine_id}", ["paused"]],
  ["get", "/api/v1/study/sessions/pending", ["limit"]],
  ["get", "/api/v1/reports/period", ["start", "end"]],
  ["get", "/api/v1/privacy/deletion-preview", ["scope"]],
];

describe("parâmetros de query que a UI manda", () => {
  for (const [method, path, params] of QUERY_USAGE) {
    it(`${method.toUpperCase()} ${path} aceita ${params.join(", ")}`, () => {
      if (!spec) return;
      const operation = spec.paths[path]?.[method];
      expect(operation, `${method} ${path} não existe no OpenAPI`).toBeDefined();

      const accepted = new Set(
        (operation.parameters ?? []).filter((p) => p.in === "query").map((p) => p.name),
      );
      const rejected = params.filter((p) => !accepted.has(p));
      expect(rejected).toEqual([]);
    });
  }
});

/*
 * ---------------------------------------------------------------
 * A CAIXA dos valores literais
 * ---------------------------------------------------------------
 *
 * Os testes acima comparam NOMES DE CAMPO, e por isso deixaram passar
 * o erro mais caro desta base até agora: `PlanItem.op`.
 *
 * O tipo declarava `"create" | "move" | ...`; a API devolve `"CREATE"`
 * / `"MOVE"`. O campo existe dos dois lados, então a verificação de
 * campos ficava verde — e o compilador também, porque o tipo mentia
 * sobre o dado. O custo não foi um rótulo em inglês na tela: **toda**
 * comparação `item.op === "move"` era falsa, e com ela sumiram, sem
 * erro nenhum, o diff de movimentação de §5.7 ("14:00 → 16:00", com o
 * horário antigo riscado) e o aviso "evento semi-flexível" de §8.1.
 *
 * O OpenAPI não resolve: o campo sai como `str` livre, sem `enum`. A
 * fonte da verdade é o `StrEnum` do Python, e é dele que este teste lê.
 */

/** Os literais de um `export type X = "a" | "b"` em `lib/types.ts`. */
function unionMembers(typeName: string): string[] {
  const source = readFileSync(join(__dirname, "..", "lib", "types.ts"), "utf8");
  const start = source.indexOf(`export type ${typeName} =`);
  if (start === -1) throw new Error(`type ${typeName} não existe em types.ts`);
  const body = source.slice(start, source.indexOf(";", start));
  return [...body.matchAll(/"([^"]+)"/g)].map((m) => m[1]);
}

/** Os valores de um `class X(StrEnum)` do backend. */
function pyEnumValues(relPath: string, className: string): string[] | null {
  let source: string;
  try {
    source = readFileSync(join(BACKEND, relPath), "utf8");
  } catch {
    return null; // Sem o backend no disco não há o que comparar.
  }
  const start = source.indexOf(`class ${className}(StrEnum):`);
  if (start === -1) throw new Error(`class ${className} não existe em ${relPath}`);
  // Até a próxima declaração na coluna 0 (outra classe, um `def`, uma
  // constante). Delimitar por linha em branco dupla não serve: `Op` e
  // a classe seguinte são separadas por UMA linha, e aí o corpo varria
  // o arquivo inteiro recolhendo os membros de todas as outras enums —
  // que foi exatamente como este parser falhou na primeira tentativa.
  // Depois da linha do `class`, senão o `^\S` casa nela mesma.
  const afterHeader = source.indexOf("\n", start) + 1;
  const rest = source.slice(afterHeader);
  const nextTop = rest.search(/^\S/m);
  const body = rest.slice(0, nextTop === -1 ? undefined : nextTop);
  return [...body.matchAll(/^\s+[A-Z_]+\s*=\s*"([^"]+)"/gm)].map((m) => m[1]);
}

const ENUM_PAIRS: [ts: string, pyFile: string, pyClass: string][] = [
  ["PlanOp", "app/domain/plan_diff.py", "Op"],
];

describe("literais de enum: a caixa tem de bater", () => {
  for (const [tsName, pyFile, pyClass] of ENUM_PAIRS) {
    it(`${tsName} cobre ${pyClass} de ${pyFile}, com a mesma caixa`, () => {
      const pyValues = pyEnumValues(pyFile, pyClass);
      if (pyValues === null) return;
      expect(pyValues.length, `${pyClass} sem membros`).toBeGreaterThan(0);

      const tsMembers = unionMembers(tsName);
      const missing = pyValues.filter((v) => !tsMembers.includes(v));
      expect(
        missing,
        `${tsName} não declara (ou declara com outra caixa): ${missing.join(", ")}`,
      ).toEqual([]);
    });
  }
});
