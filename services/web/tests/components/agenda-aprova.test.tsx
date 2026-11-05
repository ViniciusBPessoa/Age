/**
 * A agenda ganhou o botao de aprovar (§5.8, §8.1).
 *
 * Relato: "voce tem que ir la e confirmar manualmente. Mas quando voce
 * vai confirmar manualmente, nao existe o botao para confirmar."
 *
 * Era literal. Um grep por `apply` e `PlanReview` em
 * `calendar-screen.tsx` devolvia ZERO. O painel se chamava "Propostas
 * esperando aprovacao" e oferecia Remarcar, Descartar e Descartar
 * todas: todas as acoes menos aquela que o titulo prometia.
 *
 * A tela ja buscava os `Plan` inteiros e jogava o `plan.id` fora ao
 * achatar tudo em itens para a timeline desenhar. Sem o id do plano nao
 * ha para onde mandar `POST /plans/{id}/apply`.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CalendarScreen } from "@/components/calendar-screen";
import type { Plan } from "@/lib/types";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/calendar",
}));

function plano(id: string, titulo: string): Plan {
  const inicio = new Date(Date.now() + 86_400_000);
  return {
    id,
    conversation_id: null,
    origin_note: null,
    status: "proposed",
    summary: titulo,
    created_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 86_400_000).toISOString(),
    is_expired: false,
    items: [
      {
        id: "item-" + id,
        op: "create",
        target: "event",
        status: "pending",
        reason: "havia espaco livre",
        before: null,
        after: {
          title: titulo,
          start: inicio.toISOString(),
          end: new Date(inicio.getTime() + 3600_000).toISOString(),
        },
        conflicts: [],
        is_blocking: false,
        links: [],
      },
    ],
  } as unknown as Plan;
}

const chamadas: { url: string; corpo: unknown }[] = [];

function montar(planos: Plan[]) {
  chamadas.length = 0;
  global.fetch = vi.fn(async (entrada: RequestInfo | URL, init?: RequestInit) => {
    const url = String(entrada);
    if (init?.method === "POST") {
      chamadas.push({
        url,
        corpo: init.body ? JSON.parse(String(init.body)) : null,
      });
      return new Response(
        JSON.stringify({
          status: "applied",
          applied_count: 1,
          failed: [],
          manual_fix_needed: [],
          message: "Pronto.",
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }
    const corpo = url.includes("/plans") ? planos : { events: [] };
    return new Response(JSON.stringify(corpo), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;

  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <CalendarScreen timezone="America/Sao_Paulo" />
    </QueryClientProvider>,
  );
}

/** Abre o painel pelo selo do cabecalho. */
async function abrirPainel() {
  // O rotulo acessivel e "Gerenciar N propostas", nao o texto do selo.
  const selo = await screen.findByRole("button", { name: /Gerenciar/i });
  fireEvent.click(selo);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("a data de hoje so e decidida no cliente", () => {
  it("renderiza a grade depois do efeito, sem quebrar", async () => {
    /*
     * `cursor` era `useState(() => new Date())`, que roda no servidor
     * E no navegador com relogios diferentes. O React avisava "server
     * rendered HTML didn t match" apontando para `date={Date}` da
     * timeline, e o aviso so aparecia nesta rota.
     *
     * Nenhum teste olha o console: isto so foi achado capturando as
     * telas com o navegador. O teste aqui garante o contrario do que
     * quebraria se alguem voltasse a inicializar no render, porque com
     * `cursor` nulo e sem o efeito a tela ficaria vazia para sempre.
     */
    montar([]);

    // `level: 1`: a barra lateral da agenda tem subtítulos próprios
    // ("Sem horário", "Legenda"); o que este teste cobra é o título do
    // período, que só aparece quando a grade foi desenhada.
    expect(await screen.findByRole("heading", { level: 1 })).toBeTruthy();
  });
});

describe("o padrao de visualizacao cabe na tela", () => {
  it("no celular abre em dia, nao em semana", async () => {
    /*
     * Sete colunas em 390px dao 47px por dia: o nome sai cortado
     * ("18 dom", "20 terc") e nenhum titulo de evento cabe. Visto na
     * captura em 390px, nunca num teste.
     */
    vi.stubGlobal("matchMedia", (q: string) => ({
      matches: q.includes("640px"),
      media: q,
      addEventListener() {},
      removeEventListener() {},
    }));
    montar([]);

    expect(await screen.findByRole("button", { name: /^dia$/i })).toBeTruthy();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /^dia$/i }).className).toContain(
        "bg-surface",
      ),
    );
  });

  it("no desktop continua em semana", async () => {
    // Contrapeso: forcar "dia" sempre tiraria a visao de semana de
    // quem tem tela para ela.
    vi.stubGlobal("matchMedia", (q: string) => ({
      matches: false,
      media: q,
      addEventListener() {},
      removeEventListener() {},
    }));
    montar([]);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /^semana$/i }).className).toContain(
        "bg-surface",
      ),
    );
  });
});

describe("o painel de propostas permite aprovar", () => {
  it("mostra um botao de aplicar", async () => {
    /*
     * Regressao do defeito. Sabotagem: tirar o `PlanReview` do painel
     * e voltar a lista so com Remarcar e Descartar.
     */
    montar([plano("p1", "Estudo de Calculo")]);
    await abrirPainel();

    expect(await screen.findByRole("button", { name: /^Aplicar/ })).toBeTruthy();
  });

  it("aplica no plano certo, com o id do PLANO", async () => {
    /*
     * O id do item e o id do plano sao diferentes, e mandar o errado
     * da 404. Ja aconteceu neste projeto, no chat, e custou oito
     * cliques em silencio.
     */
    montar([plano("p1", "Estudo de Calculo")]);
    await abrirPainel();

    fireEvent.click(await screen.findByRole("button", { name: /^Aplicar/ }));

    await waitFor(() => expect(chamadas.length).toBeGreaterThan(0));
    expect(chamadas[0].url).toContain("/plans/p1/apply");
    expect(chamadas[0].corpo).toMatchObject({ item_ids: ["item-p1"] });
  });

  it("dois planos pendentes viram dois cartoes", async () => {
    /*
     * `confirm_blocking`, `item_ids` e a janela de undo sao POR PLANO.
     * Juntar itens de planos diferentes num cartao so mentiria sobre
     * atomicidade: o botao diria "aplicar os 2" e seriam dois pedidos.
     */
    montar([plano("p1", "Estudo de Calculo"), plano("p2", "Academia")]);
    await abrirPainel();

    const botoes = await screen.findAllByRole("button", { name: /^Aplicar/ });
    expect(botoes.length).toBe(2);
  });

  it("sem proposta, nao aparece botao de aplicar", async () => {
    // Contrapeso: um painel que sempre mostrasse o botao passaria nos
    // testes acima e ofereceria aprovar o que nao existe.
    montar([]);

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: /^Aplicar/ })).toBeNull();
  });
});
