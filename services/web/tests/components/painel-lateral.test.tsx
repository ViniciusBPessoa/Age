/**
 * O painel ao lado da conversa: tarefas e agenda sem sair do chat.
 *
 * Pedido: "enquanto eu estiver no chat, eu posso ver os to-dos que eu
 * quero ver de lado".
 *
 * As regras de dia ficam em funções puras e são testadas sem React: o
 * erro clássico aqui é de fuso (o "hoje" do UTC não é o "hoje" de quem
 * está em São Paulo às 23h30), e um teste que precisa montar a tela
 * esconderia o que está sendo verificado.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChatScreen } from "@/components/chat/chat-screen";
import {
  PainelLateral,
  agendaPorDia,
  janelaDoPainel,
  pedidoDeHorario,
  textoDoTempoLivre,
} from "@/components/painel-lateral/painel-lateral";
import { TaskRow, situacaoDoHorario } from "@/components/tasks/task-list";
import { guardarPainel, lerPainel } from "@/lib/ultimo-lugar";
import type { AgeEvent, Plan, Task } from "@/lib/types";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/chat",
}));

vi.mock("@/lib/sse", () => ({
  subscribeToTurn: () => () => {},
  toolLabel: (nome: string) => nome,
}));

const TZ = "America/Sao_Paulo";

function evento(over: Partial<AgeEvent>): AgeEvent {
  return {
    id: "e1",
    calendar_id: "c1",
    title: "Aula",
    start: "2026-11-15T12:00:00Z",
    end: "2026-11-15T13:00:00Z",
    all_day: false,
    description: null,
    location: null,
    recurrence: null,
    flexibility: "fixed",
    created_by_age: false,
    etag: null,
    color: null,
    ...over,
  };
}

function planoCom(itens: Partial<Plan["items"][number]>[]): Plan {
  return {
    id: "p1",
    conversation_id: null,
    origin_note: null,
    status: "proposed",
    summary: "",
    created_at: "2026-11-14T00:00:00Z",
    expires_at: "2026-11-20T00:00:00Z",
    is_expired: false,
    items: itens.map((i, n) => ({
      id: `i${n}`,
      target: "event",
      op: "create",
      reason: "",
      before: null,
      after: null,
      conflicts: [],
      links: [],
      status: "pending",
      is_blocking: false,
      ...i,
    })),
  } as Plan;
}

// 23h30 de sábado, 14/11, em São Paulo. Em UTC já é domingo, 15/11.
const NOITE_DE_SABADO = new Date("2026-11-15T02:30:00Z");

describe("o dia do painel é o dia da conta", () => {
  it("hoje começa à meia-noite de São Paulo, não de UTC", () => {
    // Sabotagem: `startOfDay(agora)` sem `TZDate`. No runner (UTC) a
    // janela começaria às 00:00Z do dia 15 — já amanhã para a conta.
    const { start, end } = janelaDoPainel(TZ, NOITE_DE_SABADO);
    expect(start).toBe("2026-11-14T03:00:00.000Z");
    expect(end).toBe("2026-11-16T03:00:00.000Z");
  });

  it("às 23h30, o evento das 9h de amanhã cai em Amanhã", () => {
    // Sabotagem: separar por dia em UTC. Os dois eventos são do dia 15
    // em UTC e iriam juntos para "Hoje".
    const dias = agendaPorDia(
      [
        evento({
          id: "jantar",
          start: "2026-11-15T01:00:00Z",
          end: "2026-11-15T02:00:00Z",
        }),
        evento({
          id: "aula",
          start: "2026-11-15T12:00:00Z",
          end: "2026-11-15T13:00:00Z",
        }),
      ],
      [],
      TZ,
      NOITE_DE_SABADO,
    );
    expect(dias[0].itens.map((i) => i.id)).toEqual(["jantar"]);
    expect(dias[1].itens.map((i) => i.id)).toEqual(["aula"]);
  });

  it("o que começou ontem e ainda não acabou aparece hoje", () => {
    // Plantão das 20h de sexta às 8h de sábado, olhado às 7h de sábado.
    // Sabotagem: descartar `dia < 0`. A manhã pareceria livre.
    const dias = agendaPorDia(
      [
        evento({
          id: "plantao",
          start: "2026-11-13T23:00:00Z",
          end: "2026-11-14T11:00:00Z",
        }),
      ],
      [],
      TZ,
      new Date("2026-11-14T10:00:00Z"),
    );
    expect(dias[0].itens.map((i) => i.id)).toEqual(["plantao"]);
  });

  it("dia inteiro primeiro, depois pela hora", () => {
    const dias = agendaPorDia(
      [
        evento({
          id: "tarde",
          start: "2026-11-15T18:00:00Z",
          end: "2026-11-15T19:00:00Z",
        }),
        evento({
          id: "manha",
          start: "2026-11-15T12:00:00Z",
          end: "2026-11-15T13:00:00Z",
        }),
        evento({
          id: "feriado",
          start: "2026-11-15T03:00:00Z",
          end: "2026-11-16T03:00:00Z",
          all_day: true,
        }),
      ],
      [],
      TZ,
      new Date("2026-11-15T11:00:00Z"),
    );
    expect(dias[0].itens.map((i) => i.id)).toEqual(["feriado", "manha", "tarde"]);
  });
});

describe("as propostas aparecem ao lado, marcadas", () => {
  const agora = new Date("2026-11-15T11:00:00Z");

  it("item pendente de evento entra como proposto", () => {
    // Sabotagem: ler só `/calendar/events`. Proposto não mora lá (só
    // os aplicados), e a pessoa não veria o que o Age está sugerindo.
    const dias = agendaPorDia(
      [],
      [
        planoCom([
          {
            after: {
              title: "Estudar cálculo",
              start: "2026-11-15T17:00:00Z",
              end: "2026-11-15T18:00:00Z",
            },
          },
        ]),
      ],
      TZ,
      agora,
    );
    expect(dias[0].itens).toHaveLength(1);
    expect(dias[0].itens[0]).toMatchObject({
      titulo: "Estudar cálculo",
      proposto: true,
    });
  });

  it("tarefa, item já resolvido e plano fechado ficam de fora", () => {
    const depois = {
      title: "x",
      start: "2026-11-15T17:00:00Z",
      end: "2026-11-15T18:00:00Z",
    };
    const fechado = planoCom([{ after: depois }]);
    fechado.status = "applied";
    const dias = agendaPorDia(
      [],
      [
        planoCom([
          { target: "task", after: depois },
          { status: "applied", after: depois },
        ]),
        fechado,
      ],
      TZ,
      agora,
    );
    expect(dias[0].itens).toEqual([]);
  });
});

describe("o painel guarda só o que vale", () => {
  beforeEach(() => localStorage.clear());

  it("lembra a aba e esquece ao fechar", () => {
    guardarPainel("agenda");
    expect(lerPainel()).toBe("agenda");
    guardarPainel(null);
    expect(lerPainel()).toBeNull();
  });

  it("valor estranho no armazenamento vira fechado", () => {
    localStorage.setItem("age-painel-lateral", "<script>");
    expect(lerPainel()).toBeNull();
  });
});

// ---------------------------------------------------------------
// Na tela
// ---------------------------------------------------------------

function tarefa(over: Partial<Task>): Task {
  return {
    id: "t1",
    ref_kind: "local",
    list_id: "l1",
    title: "Pagar IPVA",
    notes: null,
    due_date: null,
    due_at: null,
    status: "open",
    completed_at: null,
    priority: null,
    labels: [],
    estimated_min: null,
    position: null,
    is_overdue: false,
    reserved: [],
    reserved_minutes: 0,
    provider_name: null,
    dropped_fields: [],
    ...over,
  };
}

const json = (corpo: unknown) =>
  new Response(JSON.stringify(corpo), {
    status: 200,
    headers: { "content-type": "application/json" },
  });

/** Responde por rota, da mais específica para a mais geral. */
function servidor(tarefas: Task[]) {
  return vi.fn(async (entrada: RequestInfo | URL, init?: RequestInit) => {
    const url = String(entrada);
    const metodo = init?.method ?? "GET";
    if (url.includes("/messages") && metodo === "POST")
      return json({ turn_id: "turno-1" });
    if (url.endsWith("/conversations") && metodo === "POST")
      return json({ id: "dddddddd-1111-2222-3333-444444444444" });
    if (url.includes("/calendar/free-time"))
      return json({ free_minutes: 0, intervals: [], longest: null });
    if (url.includes("/complete")) return json({});
    if (url.includes("/tasks/lists")) return json([{ id: "l1", is_default: true }]);
    if (url.includes("/tasks") && (init?.method ?? "GET") === "POST") return json({});
    if (url.includes("/tasks")) return json(tarefas);
    if (url.includes("/calendar/external-changes"))
      return json({ changes: [], conflict_count: 0, summary_text: "" });
    if (url.includes("/calendar/events")) return json({ events: [] });
    if (url.includes("/plans")) return json([]);
    return json({ items: [] });
  });
}

function comCliente(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  vi.stubGlobal("EventSource", class {} as unknown as typeof EventSource);
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("aba Tarefas", () => {
  it("mostra a atrasada no topo e conclui com um clique", async () => {
    const fetch = servidor([
      tarefa({ id: "t-sem", title: "Ler o livro" }),
      tarefa({
        id: "t-atrasada",
        title: "Pagar IPVA",
        due_date: "2026-01-01",
        is_overdue: true,
      }),
    ]);
    vi.stubGlobal("fetch", fetch);
    comCliente(
      <PainelLateral
        aba="tarefas"
        timezone={TZ}
        onAba={() => {}}
        onFechar={() => {}}
      />,
    );

    const atrasadas = await screen.findByText("Atrasadas");
    const grupo = atrasadas.closest("section")!;
    expect(within(grupo).getByText("Pagar IPVA")).toBeTruthy();

    // No `input`, não no `label`: o jsdom não propaga clique de label.
    fireEvent.click(screen.getByRole("checkbox", { name: "Concluir Pagar IPVA" }));
    await waitFor(() =>
      expect(
        fetch.mock.calls.some(
          ([u, init]) =>
            String(u).includes("/tasks/t-atrasada/complete") && init?.method === "POST",
        ),
      ).toBe(true),
    );
  });

  it("cria uma tarefa só com o título e Enter", async () => {
    const fetch = servidor([]);
    vi.stubGlobal("fetch", fetch);
    comCliente(
      <PainelLateral
        aba="tarefas"
        timezone={TZ}
        onAba={() => {}}
        onFechar={() => {}}
      />,
    );

    // Digita e envia SEM esperar a lista padrão chegar: é o caso de
    // quem abre o painel e escreve rápido. Sabotagem: ler a lista do
    // que já está no cache, e o POST sai sem `list_id`.
    const campo = screen.getByLabelText("Nova tarefa");
    fireEvent.change(campo, { target: { value: "  Comprar café  " } });
    fireEvent.submit(campo.closest("form")!);

    await waitFor(() => {
      const post = fetch.mock.calls.find(
        ([u, init]) => String(u).endsWith("/tasks") && init?.method === "POST",
      );
      expect(post).toBeTruthy();
      expect(JSON.parse(String(post![1]!.body))).toEqual({
        title: "Comprar café",
        list_id: "l1",
      });
    });
  });
});

describe("no chat", () => {
  const ME = {
    id: "cccccccc-1111-2222-3333-444444444444",
    email: "teste@age.dev.br",
    name: "Teste",
    timezone: TZ,
    locale: "pt-BR",
    role: "user",
    email_verified: true,
    onboarding_step: 3,
  };

  function chat() {
    vi.stubGlobal("fetch", servidor([tarefa({ title: "Pagar IPVA" })]));
    return comCliente(
      <ChatScreen
        me={ME as never}
        conversationId={null}
        providers={[]}
        credentials={[]}
        keySource={null}
        calendars={[]}
        accountTarget={"event" as never}
      />,
    );
  }

  it("o botão abre o painel sem sair da conversa, e fechar esquece", async () => {
    chat();
    expect(screen.queryByRole("complementary", { name: "Painel lateral" })).toBeNull();

    fireEvent.click(
      screen.getByRole("button", { name: "Abrir tarefas e agenda ao lado" }),
    );
    const painel = screen.getByRole("complementary", { name: "Painel lateral" });
    expect(await within(painel).findByText("Pagar IPVA")).toBeTruthy();
    // A conversa continua montada: o campo de escrever segue na tela.
    expect(screen.getByPlaceholderText(/Peça algo/)).toBeTruthy();
    expect(lerPainel()).toBe("tarefas");

    fireEvent.click(within(painel).getByRole("button", { name: "Fechar painel" }));
    expect(screen.queryByRole("complementary", { name: "Painel lateral" })).toBeNull();
    expect(lerPainel()).toBeNull();
  });

  it("em tela estreita não reabre sozinho, mesmo lembrado", () => {
    // Sabotagem: restaurar sem olhar a largura. No celular o painel
    // cobriria a conversa a cada visita.
    guardarPainel("agenda");
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q }));
    chat();
    expect(screen.queryByRole("complementary", { name: "Painel lateral" })).toBeNull();
  });

  it("em tela larga reabre na aba lembrada", async () => {
    guardarPainel("agenda");
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: true, media: q }));
    chat();
    const painel = await screen.findByRole("complementary", { name: "Painel lateral" });
    expect(
      within(painel)
        .getByRole("tab", { name: /Agenda/ })
        .getAttribute("aria-selected"),
    ).toBe("true");
  });
});

// ---------------------------------------------------------------
// Horário que já passou
// ---------------------------------------------------------------
function bloco(inicio: string, fim: string) {
  return {
    event_ref_kind: "local",
    event_ref_id: `b-${inicio}`,
    title: "Relatório",
    start: inicio,
    end: fim,
    minutes: 60,
  };
}

const ONTEM = new Date(Date.now() - 24 * 3600_000);
const AMANHA = new Date(Date.now() + 24 * 3600_000);
const umaHoraDepois = (d: Date) => new Date(d.getTime() + 3600_000).toISOString();

describe("o horário reservado que passou", () => {
  it("separa sem horário, horário pela frente e horário que passou", () => {
    const agora = new Date("2026-11-15T12:00:00Z");
    expect(situacaoDoHorario({ reserved: [] }, agora)).toBe("sem");
    expect(
      situacaoDoHorario(
        { reserved: [bloco("2026-10-15T12:00:00Z", "2026-10-15T15:00:00Z")] },
        agora,
      ),
    ).toBe("passou");
    // Um passado e um futuro: ainda há horário. Sabotagem: olhar só o
    // primeiro bloco, e a tarefa com sessão amanhã viraria "passou".
    expect(
      situacaoDoHorario(
        {
          reserved: [
            bloco("2026-10-15T12:00:00Z", "2026-10-15T15:00:00Z"),
            bloco("2026-11-16T12:00:00Z", "2026-11-16T13:00:00Z"),
          ],
        },
        agora,
      ),
    ).toBe("futuro");
    // Em andamento conta como pela frente: ainda dá para fazer.
    expect(
      situacaoDoHorario(
        { reserved: [bloco("2026-11-15T11:30:00Z", "2026-11-15T12:30:00Z")] },
        agora,
      ),
    ).toBe("futuro");
  });

  it("na tela de Tarefas, o bloco passado não aparece como reservado", () => {
    // O caso real: atrasada 31 dias, com um bloco de um mês atrás em
    // verde, como se estivesse resolvida.
    const onSchedule = vi.fn();
    render(
      <ul>
        <TaskRow
          task={tarefa({
            title: "Relatório do TCC",
            due_date: "2026-01-01",
            is_overdue: true,
            reserved: [bloco(ONTEM.toISOString(), umaHoraDepois(ONTEM))],
          })}
          timezone={TZ}
          onToggle={() => {}}
          onSchedule={onSchedule}
          onDelete={() => {}}
        />
      </ul>,
    );
    expect(screen.queryByText(/reservada ·/)).toBeNull();
    expect(screen.getByText(/já passou/)).toBeTruthy();
    expect(screen.getByText(/o horário passou e a/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /reservar de novo/ }));
    expect(onSchedule).toHaveBeenCalledOnce();
  });

  it("bloco futuro continua como reservado, sem aviso", () => {
    render(
      <ul>
        <TaskRow
          task={tarefa({
            reserved: [bloco(AMANHA.toISOString(), umaHoraDepois(AMANHA))],
          })}
          timezone={TZ}
          onToggle={() => {}}
          onSchedule={() => {}}
          onDelete={() => {}}
        />
      </ul>,
    );
    expect(screen.getByText(/1h reservada ·/)).toBeTruthy();
    expect(screen.queryByText(/passou/)).toBeNull();
  });
});

// ---------------------------------------------------------------
// Pedir horário ao Age
// ---------------------------------------------------------------
describe("pedir horário a partir do painel", () => {
  it("o texto leva o título entre aspas e a estimativa", () => {
    expect(pedidoDeHorario({ title: "Pagar IPVA", estimated_min: 90 }, false)).toBe(
      'Reserva um horário na agenda para a tarefa "Pagar IPVA". Leva uns 1h30.',
    );
    expect(pedidoDeHorario({ title: "TCC", estimated_min: null }, true)).toContain(
      'reservado para "TCC" passou',
    );
  });

  function painelCom(tarefas: Task[], extra: { pedindo?: boolean } = {}) {
    const onPedir = vi.fn();
    vi.stubGlobal("fetch", servidor(tarefas));
    comCliente(
      <PainelLateral
        aba="tarefas"
        timezone={TZ}
        onAba={() => {}}
        onFechar={() => {}}
        onPedir={onPedir}
        pedindo={extra.pedindo}
      />,
    );
    return onPedir;
  }

  it("a atrasada sem horário oferece pedir, e o pedido vai para a conversa", async () => {
    const onPedir = painelCom([
      tarefa({ title: "Pagar IPVA", due_date: "2026-01-01", is_overdue: true }),
    ]);
    fireEvent.click(await screen.findByRole("button", { name: /pedir horário/ }));
    expect(onPedir).toHaveBeenCalledWith(
      'Reserva um horário na agenda para a tarefa "Pagar IPVA".',
    );
  });

  it("a de horário vencido também oferece, com o texto de remarcar", async () => {
    const onPedir = painelCom([
      tarefa({
        title: "Relatório",
        reserved: [bloco(ONTEM.toISOString(), umaHoraDepois(ONTEM))],
      }),
    ]);
    expect(await screen.findByText("o horário passou")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /pedir horário/ }));
    expect(onPedir.mock.calls[0][0]).toContain("passou e não terminei");
  });

  it("quem já tem horário pela frente não ganha botão", async () => {
    // Sabotagem: mostrar o botão para toda tarefa aberta. Viraria ruído
    // em cada linha, e o sinal de "precisa de horário" se perderia.
    painelCom([
      tarefa({
        title: "Academia",
        reserved: [bloco(AMANHA.toISOString(), umaHoraDepois(AMANHA))],
      }),
    ]);
    expect(await screen.findByText("Academia")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /pedir horário/ })).toBeNull();
  });

  it("durante a resposta do Age o botão espera", async () => {
    painelCom([tarefa({ due_date: "2026-01-01", is_overdue: true })], {
      pedindo: true,
    });
    const botao = await screen.findByRole("button", { name: /pedir horário/ });
    expect((botao as HTMLButtonElement).disabled).toBe(true);
  });
});

// ---------------------------------------------------------------
// Tempo livre
// ---------------------------------------------------------------
describe("o tempo livre de hoje", () => {
  const intervalo = (de: string, ate: string, minutos: number) => ({
    start: de,
    end: ate,
    minutes: minutos,
  });

  it("total e maior bloco, no fuso da conta", () => {
    // 22:00Z é 19:00 em São Paulo. Sabotagem: formatar em UTC.
    const texto = textoDoTempoLivre(
      {
        free_minutes: 200,
        intervals: [
          intervalo("2026-11-15T17:00:00Z", "2026-11-15T17:20:00Z", 20),
          intervalo("2026-11-15T22:00:00Z", "2026-11-16T01:00:00Z", 180),
        ],
        longest: intervalo("2026-11-15T22:00:00Z", "2026-11-16T01:00:00Z", 180),
      },
      TZ,
    );
    expect(texto).toBe("3h20 livres no resto do dia · maior bloco 19:00–22:00 (3h).");
  });

  it("um bloco só diz de quando a quando", () => {
    const i = intervalo("2026-11-15T22:00:00Z", "2026-11-16T00:00:00Z", 120);
    expect(
      textoDoTempoLivre({ free_minutes: 120, intervals: [i], longest: i }, TZ),
    ).toBe("2h livres no resto do dia, das 19:00 às 21:00.");
  });

  it("dia cheio diz que não sobra nada", () => {
    expect(
      textoDoTempoLivre({ free_minutes: 0, intervals: [], longest: null }, TZ),
    ).toMatch(/Nada livre/);
  });
});

// ---------------------------------------------------------------
// Atalho, gaveta e pedido no chat
// ---------------------------------------------------------------
describe("no chat, o resto", () => {
  const ME = {
    id: "cccccccc-1111-2222-3333-444444444444",
    email: "teste@age.dev.br",
    name: "Teste",
    timezone: TZ,
    locale: "pt-BR",
    role: "user",
    email_verified: true,
    onboarding_step: 3,
  };

  function chat(tarefas: Task[] = [tarefa({ title: "Pagar IPVA" })]) {
    const fetch = servidor(tarefas);
    vi.stubGlobal("fetch", fetch);
    comCliente(
      <ChatScreen
        me={ME as never}
        conversationId={null}
        providers={[]}
        credentials={[]}
        keySource={null}
        calendars={[]}
        accountTarget={"event" as never}
      />,
    );
    return fetch;
  }

  it("Ctrl+B abre e fecha o painel, mesmo digitando", () => {
    chat();
    const campo = screen.getByPlaceholderText(/Peça algo/);
    fireEvent.keyDown(campo, { key: "b", ctrlKey: true });
    expect(screen.getByRole("complementary", { name: "Painel lateral" })).toBeTruthy();
    fireEvent.keyDown(campo, { key: "b", ctrlKey: true });
    expect(screen.queryByRole("complementary", { name: "Painel lateral" })).toBeNull();
    // Sem modificador é texto, não atalho.
    fireEvent.keyDown(campo, { key: "b" });
    expect(screen.queryByRole("complementary", { name: "Painel lateral" })).toBeNull();
  });

  it("o botão de conversas abre a lista como gaveta", () => {
    chat();
    const lista = screen.getByText("Nova conversa").closest("div.shrink-0")!;
    const classes = () => lista.className.split(/\s+/);
    expect(classes()).toContain("hidden");
    fireEvent.click(screen.getByRole("button", { name: "Abrir conversas" }));
    expect(classes()).not.toContain("hidden");
    expect(classes()).toContain("fixed");
  });

  it("pedir horário no painel vira mensagem na conversa", async () => {
    const fetch = chat([
      tarefa({ title: "Pagar IPVA", due_date: "2026-01-01", is_overdue: true }),
    ]);
    fireEvent.click(
      screen.getByRole("button", { name: "Abrir tarefas e agenda ao lado" }),
    );
    fireEvent.click(await screen.findByRole("button", { name: /pedir horário/ }));
    await waitFor(() => {
      const envio = fetch.mock.calls.find(
        ([u, init]) => String(u).includes("/messages") && init?.method === "POST",
      );
      expect(envio).toBeTruthy();
      expect(JSON.parse(String(envio![1]!.body)).content).toBe(
        'Reserva um horário na agenda para a tarefa "Pagar IPVA".',
      );
    });
  });
});
