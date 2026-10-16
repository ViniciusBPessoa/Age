/**
 * Ajustar a proposta antes de aprovar (§5.8).
 *
 * O cartão sabia marcar e desmarcar item. O que faltava era mexer no
 * conteúdo: "esse bloco serve, mas não às 8h15" obrigava a rejeitar
 * tudo e redigitar o pedido, gastando um turno do modelo para mover
 * meia hora.
 *
 * Duas saídas, porque os pedidos são de dois tipos:
 *
 *  - mecânico ("às 10h, 1h de duração") vira campo de horário, que
 *    resolve na hora e sem custo de API;
 *  - em palavras ("tira os de sábado") vira mensagem para o Age, que
 *    é a única forma de refazer o raciocínio.
 *
 * A armadilha que estes testes guardam: o ajuste mora DENTRO de um
 * `<li>` cujo corpo é um `<label>` da caixa de marcação. Qualquer
 * clique dentro do label alterna a caixa, então abrir o ajuste
 * desmarcaria justamente o item que a pessoa quer manter.
 */

import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PlanReview } from "@/components/plan-review/plan-review";
import type { Plan, PlanItem } from "@/lib/types";

const TZ = "America/Sao_Paulo";

function item(over: Partial<PlanItem> = {}): PlanItem {
  return {
    id: "item-1",
    target: "event",
    op: "CREATE",
    before: null,
    after: {
      title: "Álgebra linear: vetores",
      start: "2026-10-20T08:15:00-03:00",
      end: "2026-10-20T09:15:00-03:00",
    },
    reason: "primeiro tópico da sequência",
    conflicts: [],
    links: [],
    status: "pending",
    is_blocking: false,
    ...over,
  };
}

function plano(items: PlanItem[]): Plan {
  return {
    id: "plano-1",
    conversation_id: "conversa-1",
    status: "proposed",
    summary: "agenda: 1 para criar",
    items,
    created_at: "2026-10-16T05:00:00Z",
    expires_at: "2026-10-17T05:00:00Z",
    origin_note: null,
    is_expired: false,
  };
}

async function clicar(alvo: HTMLElement) {
  await act(async () => {
    fireEvent.click(alvo);
  });
}

function montar(
  over: {
    onReschedule?: (id: string, s: string, e: string) => Promise<void>;
    onAskChange?: (t: string) => void;
    items?: PlanItem[];
  } = {},
) {
  const props = {
    plan: plano(over.items ?? [item()]),
    timezone: TZ,
    onApply: vi.fn(),
    onReject: vi.fn(),
    onReschedule: over.onReschedule,
    onAskChange: over.onAskChange,
  };
  render(<PlanReview {...props} />);
  return props;
}

describe("ajuste de horário por item", () => {
  it("não aparece quando a tela não sabe remarcar", () => {
    // A mesma PlanReview é usada em mais de um lugar. Oferecer um botão
    // que não faz nada é pior do que não oferecer.
    montar();
    expect(screen.queryByText("Ajustar horário")).toBeNull();
  });

  it("abre os campos com os valores atuais do item", async () => {
    // Preenchido, não em branco: a pessoa quer mudar meia hora, não
    // redigitar o compromisso.
    montar({ onReschedule: vi.fn() });
    await clicar(screen.getByText("Ajustar horário"));

    const quando = screen.getByLabelText("Começa") as HTMLInputElement;
    const minutos = screen.getByLabelText("Minutos") as HTMLInputElement;

    expect(quando.value).toBe("2026-10-20T08:15");
    expect(minutos.value).toBe("60");
  });

  it("o botão de ajuste fica FORA do label da caixa de marcação", () => {
    /*
     * Verificação estrutural, e de propósito.
     *
     * No navegador, clicar em qualquer lugar dentro de um `<label>`
     * alterna o controle associado: abrir o ajuste desmarcaria o item
     * que a pessoa está justamente querendo manter.
     *
     * Escrevi primeiro o teste comportamental óbvio (clicar e conferir
     * se a caixa continua marcada) e ele passou MESMO com o painel
     * movido para dentro do label, porque o jsdom não reproduz essa
     * propagação. Teste verde que não pega a falha é pior que teste
     * nenhum: dá confiança falsa exatamente no ponto arriscado.
     *
     * Então a asserção é sobre a árvore, que é onde o problema vive.
     */
    const { container } = render(
      <PlanReview
        plan={plano([item()])}
        timezone={TZ}
        onApply={vi.fn()}
        onReject={vi.fn()}
        onReschedule={vi.fn()}
      />,
    );

    const botao = screen.getByText("Ajustar horário");
    const label = container.querySelector("li label");

    expect(label).toBeTruthy();
    expect(label?.contains(botao)).toBe(false);
  });

  it("salva início e fim em ISO, com a duração aplicada", async () => {
    // O backend recebe instantes absolutos; o campo é hora local. A
    // conversão é o ponto onde um fuso errado estragaria tudo.
    const onReschedule = vi.fn().mockResolvedValue(undefined);
    montar({ onReschedule });
    await clicar(screen.getByText("Ajustar horário"));

    await act(async () => {
      fireEvent.change(screen.getByLabelText("Começa"), {
        target: { value: "2026-10-20T10:00" },
      });
      fireEvent.change(screen.getByLabelText("Minutos"), {
        target: { value: "90" },
      });
    });
    await clicar(screen.getByRole("button", { name: "Salvar" }));

    expect(onReschedule).toHaveBeenCalledTimes(1);
    const [id, inicio, fim] = onReschedule.mock.calls[0];
    expect(id).toBe("item-1");
    // 90 minutos exatos entre os dois instantes.
    expect(new Date(fim).getTime() - new Date(inicio).getTime()).toBe(90 * 60 * 1000);
  });

  it("cancelar não chama nada", async () => {
    const onReschedule = vi.fn();
    montar({ onReschedule });
    await clicar(screen.getByText("Ajustar horário"));
    await clicar(screen.getByRole("button", { name: "Cancelar" }));

    expect(onReschedule).not.toHaveBeenCalled();
    expect(screen.getByText("Ajustar horário")).toBeTruthy();
  });

  it("não oferece ajuste de horário para item de lista", () => {
    // Tarefa tem prazo, não intervalo. Um campo de "minutos" ali não
    // significa nada.
    montar({
      onReschedule: vi.fn(),
      items: [
        item({
          target: "task",
          after: { title: "Comprar caderno", due_date: "2026-10-22" },
        }),
      ],
    });

    expect(screen.queryByText("Ajustar horário")).toBeNull();
  });
});

describe("pedir mudança em palavras", () => {
  it("não aparece quando a tela não sabe encaminhar", () => {
    montar();
    expect(screen.queryByText("Pedir mudança")).toBeNull();
  });

  it("manda o texto e fecha o campo", async () => {
    const onAskChange = vi.fn();
    montar({ onAskChange });

    await clicar(screen.getByRole("button", { name: "Pedir mudança" }));
    await act(async () => {
      fireEvent.change(screen.getByLabelText(/O que você quer diferente/), {
        target: { value: "tira os de sábado" },
      });
    });
    await clicar(screen.getByRole("button", { name: "Pedir" }));

    expect(onAskChange).toHaveBeenCalledWith("tira os de sábado");
  });

  it("Enter envia, porque a frase é curta", async () => {
    // Quem escreve "prefiro de manhã" não quer procurar botão.
    const onAskChange = vi.fn();
    montar({ onAskChange });

    await clicar(screen.getByRole("button", { name: "Pedir mudança" }));
    const campo = screen.getByLabelText(/O que você quer diferente/);
    await act(async () => {
      fireEvent.change(campo, { target: { value: "prefiro de manhã" } });
      fireEvent.keyDown(campo, { key: "Enter", shiftKey: false });
    });

    expect(onAskChange).toHaveBeenCalledWith("prefiro de manhã");
  });

  it("Shift+Enter não envia", async () => {
    // Senão não há como escrever duas linhas.
    const onAskChange = vi.fn();
    montar({ onAskChange });

    await clicar(screen.getByRole("button", { name: "Pedir mudança" }));
    const campo = screen.getByLabelText(/O que você quer diferente/);
    await act(async () => {
      fireEvent.change(campo, { target: { value: "primeira linha" } });
      fireEvent.keyDown(campo, { key: "Enter", shiftKey: true });
    });

    expect(onAskChange).not.toHaveBeenCalled();
  });

  it("não manda texto vazio", async () => {
    const onAskChange = vi.fn();
    montar({ onAskChange });

    await clicar(screen.getByRole("button", { name: "Pedir mudança" }));
    const botao = screen.getByRole("button", { name: "Pedir" });

    expect((botao as HTMLButtonElement).disabled).toBe(true);
  });
});
