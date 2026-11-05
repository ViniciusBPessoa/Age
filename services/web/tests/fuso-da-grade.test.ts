/**
 * A grade desenha no fuso da CONTA, qualquer que seja o do navegador.
 *
 * O defeito que este teste cerca: `toLocal` fazia
 * `new Date(tzDate.toString())`, e o texto trazia o deslocamento, então
 * a conversão se anulava. Com o navegador em UTC (o caso do CI e de
 * quem viaja), a aula das 08:00 de São Paulo aparecia às 11:00.
 *
 * O vitest roda com o fuso do processo; os testes usam um fuso de conta
 * diferente de UTC e de qualquer fuso brasileiro para que o defeito
 * apareça em qualquer máquina.
 */

import { describe, expect, it } from "vitest";

import { daGrade, paraGrade } from "@/lib/fuso-da-grade";

describe("paraGrade: a hora de parede da conta", () => {
  it("08:00 em São Paulo aparece como 08:00 na grade", () => {
    const d = paraGrade("2026-11-02T11:00:00Z", "America/Sao_Paulo");
    expect([d.getHours(), d.getMinutes()]).toEqual([8, 0]);
    expect(d.getDate()).toBe(2);
  });

  it("vale para um fuso do outro lado do mundo", () => {
    const d = paraGrade("2026-11-02T11:00:00Z", "Asia/Tokyo");
    expect([d.getDate(), d.getHours()]).toEqual([2, 20]);
  });

  it("vira o dia quando o fuso vira o dia", () => {
    const d = paraGrade("2026-11-03T01:30:00Z", "America/Sao_Paulo");
    expect([d.getDate(), d.getHours(), d.getMinutes()]).toEqual([2, 22, 30]);
  });
});

describe("daGrade: o inverso, para gravar o que foi arrastado", () => {
  it("14:00 solto na grade é 14:00 em São Paulo (17:00 UTC)", () => {
    const solto = new Date(2026, 10, 5, 14, 0);
    expect(daGrade(solto, "America/Sao_Paulo").toISOString()).toBe(
      "2026-11-05T17:00:00.000Z",
    );
  });

  it("ida e volta devolve o mesmo instante", () => {
    for (const iso of [
      "2026-11-02T11:00:00.000Z",
      "2026-02-15T02:30:00.000Z",
      "2026-07-31T23:59:00.000Z",
    ]) {
      for (const tz of ["America/Sao_Paulo", "Europe/Lisbon", "Asia/Tokyo", "UTC"]) {
        expect(daGrade(paraGrade(iso, tz), tz).toISOString(), `${iso} ${tz}`).toBe(iso);
      }
    }
  });
});
