/**
 * O cartao de aprovacao sobrevive a montagem da tela (§5.2, §8.1).
 *
 * Este e o teste que `cartao-aparece.test.tsx` prometia e nao tinha. Ele
 * monta a `ChatScreen` de verdade, com react-query e `fetch` falso, e
 * afirma sobre a arvore renderizada.
 *
 * O defeito que ele tranca: a consulta do plano era
 * `enabled: turnoComPlano !== null`, e `turnoComPlano` era preenchido
 * num unico lugar, o evento SSE `plan`. Nada o restaurava. Dar F5,
 * trocar de tela ou mandar a mensagem seguinte apagava o cartao para
 * sempre, mesmo com o plano vivo e `proposed` no banco, respondendo em
 * `GET /plans?status=proposed`. A tela so parava de perguntar.
 *
 * "Voce nao tem como confirmar que voce quer marcar alguma coisa pelo
 * chat [...] tu nunca confirma porra nenhuma" foi o relato.
 *
 * Por isso cada caso aqui monta a tela SEM nenhum evento SSE. Se o
 * cartao aparecer assim, ele nao depende mais do turno.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChatScreen } from "@/components/chat/chat-screen";
import type { Plan } from "@/lib/types";

/*
 * `useRouter` vem do Next e nao existe fora dele. A tela chama
 * `replace` e `push`, e nenhum dos dois e o que estes testes olham.
 */
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

/*
 * O SSE abre um `EventSource`, que o jsdom nao tem. Nenhum caso aqui
 * dispara turno, entao basta existir: sem o stub, o modulo quebra ao
 * ser carregado e a falha parece do componente.
 */
vi.mock("@/lib/sse", () => ({
  subscribeToTurn: () => () => {},
  toolLabel: (nome: string) => nome,
}));

const CONVERSA = "aaaaaaaa-1111-2222-3333-444444444444";
const OUTRA = "bbbbbbbb-1111-2222-3333-444444444444";

/* Uma mensagem com a forma completa: faltar campo aqui quebra o
 * `MessageList` e o teste acusaria "cartao nao apareceu" por um
 * motivo que nao tem nada a ver com o cartao. */
const MENSAGEM = {
  id: "eeeeeeee-1111-2222-3333-444444444444",
  role: "user" as const,
  content: "organiza meus estudos de Python",
  created_at: new Date().toISOString(),
  tool_calls: [],
  applied_plan_id: null,
  token_count: null,
};

const ME = {
  id: "cccccccc-1111-2222-3333-444444444444",
  email: "teste@age.dev.br",
  name: "Teste",
  timezone: "America/Sao_Paulo",
  locale: "pt-BR",
  role: "user",
  email_verified: true,
  onboarding_step: 3,
};

function plano(conversationId: string | null): Plan {
  const agora = new Date();
  const daqui = new Date(agora.getTime() + 3 * 3600_000);
  return {
    id: "11111111-2222-3333-4444-555555555555",
    conversation_id: conversationId,
    origin_note: null,
    status: "proposed",
    summary: "3 sessoes de Python",
    created_at: agora.toISOString(),
    expires_at: new Date(agora.getTime() + 24 * 3600_000).toISOString(),
    is_expired: false,
    items: [
      {
        id: "99999999-2222-3333-4444-555555555555",
        op: "create",
        target: "event",
        status: "pending",
        reason: "bloco livre na agenda",
        before: null,
        after: {
          title: "Funcoes em Python",
          start: daqui.toISOString(),
          end: new Date(daqui.getTime() + 5400_000).toISOString(),
        },
        conflicts: [],
        is_blocking: false,
        links: [],
      },
    ],
  } as unknown as Plan;
}

/*
 * Respostas por rota.
 *
 * O padrao de cada rota nao conferida importa: a tela le
 * `changes.data.changes.length` sem guarda, entao devolver `{items: []}`
 * para `/calendar/external-changes` derruba a arvore inteira e o teste
 * acusa "cartao nao apareceu" quando o que houve foi um TypeError.
 */
const PADRAO: Record<string, unknown> = {
  "/calendar/external-changes": {
    changes: [],
    conflict_count: 0,
    summary_text: "",
  },
};

function responder(rotas: Record<string, unknown>) {
  return vi.fn(async (entrada: RequestInfo | URL) => {
    const url = String(entrada);
    for (const [trecho, corpo] of Object.entries({ ...PADRAO, ...rotas })) {
      if (url.includes(trecho)) {
        return new Response(JSON.stringify(corpo), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
    }
    return new Response(JSON.stringify({ items: [] }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
}

function montar(rotas: Record<string, unknown>, conversationId = CONVERSA) {
  // `retry: false` para a falha aparecer em vez de ser engolida por
  // tentativas, que foi exatamente o modo de falha que origina o bug.
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  global.fetch = responder(rotas) as typeof fetch;
  return render(
    <QueryClientProvider client={client}>
      <ChatScreen
        me={ME}
        conversationId={conversationId}
        providers={[]}
        credentials={[]}
        keySource={null}
        calendars={[]}
        accountTarget={"event" as never}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal("EventSource", class {} as unknown as typeof EventSource);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("a proposta pendente aparece sem evento de turno", () => {
  it("monta a tela e mostra o cartao", async () => {
    /*
     * Regressao do defeito principal. Sabotagem que precisa derruba-lo:
     * voltar a consulta para `enabled: turnoComPlano !== null`.
     */
    montar({
      "/plans": [plano(CONVERSA)],
      "/messages": { items: [MENSAGEM] },
    });

    expect(await screen.findByText(/3 sessoes de Python/)).toBeTruthy();
  });

  it("mostra o cartao mesmo sem mensagem carregada", async () => {
    /*
     * O segundo caminho, independente da consulta. O estado vazio
     * renderiza NO LUGAR do `MessageList`, e o cartao e filho do
     * `MessageList`: com a lista de mensagens vazia, a tela caia no
     * estado vazio e escondia uma proposta ja carregada.
     *
     * Sabotagem: tirar `plan.data != null` de `mostraEstadoVazio`.
     */
    montar({
      "/plans": [plano(CONVERSA)],
      "/messages": { items: [] },
    });

    expect(await screen.findByText(/3 sessoes de Python/)).toBeTruthy();
  });

  it("proposta de outra conversa nao aparece aqui", async () => {
    /*
     * Contrapeso: "mostrar sempre que houver proposta" passaria nos
     * dois casos acima e vazaria o cartao de uma conversa para outra.
     */
    montar({
      "/plans": [plano(OUTRA)],
      "/messages": { items: [MENSAGEM] },
    });

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(screen.queryByText(/3 sessoes de Python/)).toBeNull();
  });

  it("sem proposta nenhuma, nao inventa cartao", async () => {
    montar({
      "/plans": [],
      "/messages": { items: [MENSAGEM] },
    });

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(screen.queryByText(/3 sessoes de Python/)).toBeNull();
  });
});
