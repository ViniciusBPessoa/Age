/**
 * A tela de reservar tempo, refeita (§8.1).
 *
 * Cada teste traz a sabotagem que precisa derrubá-lo.
 *
 * O relato: *"esse menu tá uma porcaria. (...) ele não permite escolher
 * minha própria hora caso eu queira. Ele só dá uma lista de horas. O
 * menu de lista de horas (...) é um pedaço de bosta."*
 *
 * A versão anterior pedia a duração e devolvia só sugestões do
 * `Scheduler` — não havia campo de horário em lugar nenhum.
 *
 * Os helpers de data são testados **puros**, sem montar o diálogo: é
 * onde mora o erro clássico de fuso, e um teste que precisa de React
 * para exercitá-lo esconde o que está sendo verificado.
 */

import { describe, expect, it } from "vitest";

import {
  conflitosCom,
  instanteDosCampos,
  paraCampoData,
  paraCampoHora,
} from "@/components/tasks/reserve-time-dialog";
import type { AgeEvent } from "@/lib/types";

function evento(inicio: string, fim: string, titulo = "Aula"): AgeEvent {
  return {
    id: "e1",
    calendar_id: "c1",
    title: titulo,
    start: inicio,
    end: fim,
    all_day: false,
    description: null,
    location: null,
    recurrence: null,
    flexibility: "fixed",
    created_by_age: false,
    etag: null,
  } as AgeEvent;
}

describe("os campos de data e hora", () => {
  it("formata no fuso LOCAL, não em UTC", () => {
    /*
     * A sabotagem que só este teste pega: usar
     * `toISOString().slice(0, 10)`.
     *
     * Em São Paulo (UTC-3), 21h do dia 25 vira 00h do dia 26 em UTC — o
     * campo mostraria o dia seguinte. É o erro de fuso mais comum em
     * agenda, e ele só aparece à noite.
     */
    const noite = new Date(2026, 9, 25, 21, 30); // 25/10/2026, 21h30 local
    expect(paraCampoData(noite)).toBe("2026-10-25");
    expect(paraCampoHora(noite)).toBe("21:30");
  });

  it("zero à esquerda em mês, dia e hora", () => {
    // Sabotagem: concatenar sem `padStart`. `2026-1-5` e `9:5` não são
    // valores válidos para `<input type="date">` nem `type="time"`, e o
    // campo abriria vazio.
    const cedo = new Date(2026, 0, 5, 9, 5);
    expect(paraCampoData(cedo)).toBe("2026-01-05");
    expect(paraCampoHora(cedo)).toBe("09:05");
  });

  it("lê de volta o que foi digitado, sem mover a hora", () => {
    /*
     * Sabotagem: montar a data com `Z` no fim
     * (`new Date("2026-10-26T14:00Z")`).
     *
     * Isso trataria 14h como UTC, e o bloco entraria às 11h na agenda
     * de quem está em São Paulo. A pessoa digita hora local.
     */
    const d = instanteDosCampos("2026-10-26", "14:00")!;
    expect(d.getHours()).toBe(14);
    expect(d.getMinutes()).toBe(0);
    expect(d.getDate()).toBe(26);
  });

  it("campo vazio ou inválido não vira data", () => {
    // Sabotagem: devolver `new Date("")`, que é `Invalid Date` e
    // viajaria até o servidor como `null` silencioso.
    expect(instanteDosCampos("", "14:00")).toBeNull();
    expect(instanteDosCampos("2026-10-26", "")).toBeNull();
    expect(instanteDosCampos("nao-e-data", "14:00")).toBeNull();
  });

  it("ida e volta pelos campos preserva o instante", () => {
    const original = new Date(2026, 10, 8, 18, 15);
    const voltou = instanteDosCampos(
      paraCampoData(original),
      paraCampoHora(original),
    )!;
    expect(voltou.getTime()).toBe(original.getTime());
  });
});

describe("o aviso de conflito", () => {
  const AULA = evento(
    "2026-10-26T16:00:00-03:00",
    "2026-10-26T18:00:00-03:00",
    "Aula de Cálculo",
  );

  it("detecta sobreposição parcial", () => {
    /*
     * A sabotagem central: não checar conflito nenhum.
     *
     * Era o argumento da versão anterior para NÃO ter campo manual —
     * "quem digita horário à mão escolhe sem saber que vai colidir com
     * a aula das 16h". O campo existe agora porque o aviso existe.
     */
    const inicio = new Date("2026-10-26T17:00:00-03:00");
    const fim = new Date("2026-10-26T18:30:00-03:00");
    expect(conflitosCom([AULA], inicio, fim)).toHaveLength(1);
  });

  it("detecta bloco contido dentro de outro", () => {
    // Sabotagem: comparar só as bordas. Uma reserva de 16h30 às 17h
    // cabe inteira dentro da aula e não toca nenhuma borda.
    const inicio = new Date("2026-10-26T16:30:00-03:00");
    const fim = new Date("2026-10-26T17:00:00-03:00");
    expect(conflitosCom([AULA], inicio, fim)).toHaveLength(1);
  });

  it("encostar NÃO é colidir", () => {
    /*
     * Sabotagem: usar `<=` / `>=`.
     *
     * Reservar 18h-19h logo depois de uma aula que termina às 18h é
     * exatamente o que a pessoa quer fazer. Avisar aqui treinaria
     * qualquer um a ignorar o aviso.
     */
    const inicio = new Date("2026-10-26T18:00:00-03:00");
    const fim = new Date("2026-10-26T19:00:00-03:00");
    expect(conflitosCom([AULA], inicio, fim)).toHaveLength(0);
  });

  it("outro dia não conflita", () => {
    const inicio = new Date("2026-10-27T16:00:00-03:00");
    const fim = new Date("2026-10-27T17:00:00-03:00");
    expect(conflitosCom([AULA], inicio, fim)).toHaveLength(0);
  });

  it("agenda vazia não inventa conflito", () => {
    const inicio = new Date("2026-10-26T16:00:00-03:00");
    const fim = new Date("2026-10-26T17:00:00-03:00");
    expect(conflitosCom([], inicio, fim)).toHaveLength(0);
  });

  it("lista todos os que colidem, não só o primeiro", () => {
    /*
     * Sabotagem: devolver no primeiro achado.
     *
     * Um bloco longo pode cruzar dois compromissos, e dizer só um faria
     * a pessoa achar que resolveu ao desviar do primeiro.
     */
    const outro = evento(
      "2026-10-26T17:30:00-03:00",
      "2026-10-26T19:00:00-03:00",
      "Reunião",
    );
    const inicio = new Date("2026-10-26T15:00:00-03:00");
    const fim = new Date("2026-10-26T20:00:00-03:00");
    expect(conflitosCom([AULA, outro], inicio, fim)).toHaveLength(2);
  });
});
