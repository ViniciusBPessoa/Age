/**
 * Formatação de data e hora. **Só formatação.**
 *
 * ARQUITETURA.md §9.5: todo cálculo de tempo acontece no backend. Este
 * módulo converte instante em texto, e nada mais — não decide horário,
 * não soma duração, não procura conflito.
 *
 * O fuso vem sempre de fora, de `users.timezone`, e nunca do browser.
 * A razão é concreta: o M5 teve um bug em que o relatório saía em UTC,
 * e o usuário via "17:00" para um evento das 14:00. A conclusão dele
 * não seria "o fuso está errado", seria "o sistema está errado sobre a
 * minha agenda" — e aí nada mais na tela merece confiança. Usar o fuso
 * do browser teria o mesmo efeito para quem viaja: a agenda inteira se
 * desloca sem ninguém pedir.
 *
 * `date-fns` + `@date-fns/tz`, não Moment nem Day.js (§2.4): Moment é
 * mutável e deprecado, e o suporte a fuso do Day.js depende de plugin.
 * Num app cujo domínio é horário, a aritmética precisa ser imutável.
 */

import { TZDate } from "@date-fns/tz";
import { differenceInCalendarDays, format, isSameDay, isSameYear } from "date-fns";
import { ptBR } from "date-fns/locale";

const LOCALE = { locale: ptBR };

/** Converte um ISO do backend num instante no fuso da conta. */
export function inZone(iso: string, timezone: string): TZDate {
  return new TZDate(new Date(iso), timezone);
}

/** `14:00` */
export function timeOf(iso: string, timezone: string): string {
  return format(inZone(iso, timezone), "HH:mm", LOCALE);
}

/** `qua 09/10` — dia da semana abreviado em pt-BR, como §5.9.4 escreve. */
export function dayOf(iso: string, timezone: string): string {
  return format(inZone(iso, timezone), "EEE dd/MM", LOCALE);
}

/** `qua 09/10 · 14:00` */
export function moment(iso: string, timezone: string): string {
  return `${dayOf(iso, timezone)} · ${timeOf(iso, timezone)}`;
}

/**
 * `qua 09/10 · 14:00–15:30`, ou com a data repetida se cruzar meia-noite.
 *
 * O caso de cruzar o dia não é teórico: um bloco que começa 23:00 e
 * dura 2h termina no dia seguinte, e escrever "23:00–01:00" sem dizer
 * qual dia é informação errada.
 */
export function range(startIso: string, endIso: string, timezone: string): string {
  const start = inZone(startIso, timezone);
  const end = inZone(endIso, timezone);
  const head = `${dayOf(startIso, timezone)} · ${format(start, "HH:mm", LOCALE)}`;
  if (isSameDay(start, end)) return `${head}–${format(end, "HH:mm", LOCALE)}`;
  return `${head} – ${dayOf(endIso, timezone)} · ${format(end, "HH:mm", LOCALE)}`;
}

/** `3h`, `90min`, `1h30` — o formato que a conversa usa (§5.9.12). */
export function duration(minutes: number): string {
  if (minutes < 60) return `${minutes}min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (rest === 0) return `${hours}h`;
  return `${hours}h${String(rest).padStart(2, "0")}`;
}

/**
 * Prazo em linguagem de prazo: `vence hoje`, `vence em 3 dias`,
 * `atrasada 2 dias`.
 *
 * Usa `differenceInCalendarDays`, não diferença de horas, porque "vence
 * amanhã" é sobre o calendário e não sobre 24h: às 23h de hoje, um
 * prazo para as 9h de amanhã é "amanhã", não "em 10 horas".
 */
export function dueLabel(
  iso: string,
  timezone: string,
  now: Date = new Date(),
): string {
  const days = daysUntil(iso, timezone, now);

  if (days === 0) return "vence hoje";
  if (days === 1) return "vence amanhã";
  if (days === -1) return "atrasada 1 dia";
  if (days < -1) return `atrasada ${Math.abs(days)} dias`;
  if (days <= 7) return `vence em ${days} dias`;
  return `vence ${dateOf(iso, timezone)}`;
}

/**
 * Dias de calendário até o prazo. Negativo quando já passou.
 *
 * É a primitiva por trás de `dueLabel` e de qualquer decisão sobre
 * proximidade de prazo — a regra "⚠ se vence em até 7 dias e não tem
 * tempo reservado" (§8.1) precisa do número, não da frase.
 *
 * Dias de calendário, e não diferença de horas, porque "vence amanhã" é
 * sobre o calendário: às 23h de hoje, um prazo para as 9h de amanhã é
 * amanhã, não "em 10 horas".
 */
export function daysUntil(
  iso: string,
  timezone: string,
  now: Date = new Date(),
): number {
  return differenceInCalendarDays(inZone(iso, timezone), new TZDate(now, timezone));
}

/** `20/10`, ou `20/10/2027` se cair em outro ano. */
export function dateOf(iso: string, timezone: string, now: Date = new Date()): string {
  const value = inZone(iso, timezone);
  const today = new TZDate(now, timezone);
  return format(value, isSameYear(value, today) ? "dd/MM" : "dd/MM/yyyy", LOCALE);
}

/**
 * Agrupamento da sidebar: `Hoje`, `Últimos 7 dias`, `Anteriores` (§8.1).
 *
 * O backend não devolve isso pronto porque depende do fuso de quem
 * olha, e a mesma conversa pode estar em "hoje" ou "ontem" dependendo
 * do fuso da conta — então é classificação de apresentação, não de
 * domínio.
 */
export type RecencyBucket = "hoje" | "7dias" | "anteriores";

export function recencyBucket(
  iso: string,
  timezone: string,
  now: Date = new Date(),
): RecencyBucket {
  const days = -differenceInCalendarDays(
    inZone(iso, timezone),
    new TZDate(now, timezone),
  );
  if (days <= 0) return "hoje";
  if (days <= 7) return "7dias";
  return "anteriores";
}

export const BUCKET_LABEL: Record<RecencyBucket, string> = {
  hoje: "Hoje",
  "7dias": "Últimos 7 dias",
  anteriores: "Anteriores",
};

/** `R$ 0,05` — o custo estimado que o onboarding mostra (§8.1). */
export function brl(value: number): string {
  return value.toLocaleString("pt-BR", {
    style: "currency",
    currency: "BRL",
    minimumFractionDigits: 2,
  });
}

/** `67%` a partir de 0.667. */
export function percent(fraction: number): string {
  return `${Math.round(fraction * 100)}%`;
}
