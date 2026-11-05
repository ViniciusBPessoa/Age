/**
 * As duas conversões de fuso da grade da agenda.
 *
 * A biblioteca da grade só conhece `Date` do navegador, que desenha na
 * hora LOCAL do navegador. A agenda, porém, é desenhada no fuso da CONTA
 * (§9.5): quem mora em São Paulo e abre a agenda em Lisboa continua
 * vendo a aula às 08:00. Então:
 *
 * - `paraGrade`: instante (ISO) -> `Date` cuja hora local é a hora de
 *   parede no fuso da conta;
 * - `daGrade`: o inverso, para gravar o que foi arrastado ou clicado.
 *
 * As duas montam a data campo a campo. A versão antiga usava
 * `new Date(tzDate.toString())`, e o texto carregava o deslocamento
 * ("GMT-0300"), o que devolvia o mesmo instante e anulava a conversão:
 * num navegador em UTC, a aula das 08:00 aparecia às 11:00.
 */

import { TZDate } from "@date-fns/tz";

export function paraGrade(iso: string | Date, timezone: string): Date {
  const z = new TZDate(new Date(iso), timezone);
  return new Date(
    z.getFullYear(),
    z.getMonth(),
    z.getDate(),
    z.getHours(),
    z.getMinutes(),
    z.getSeconds(),
  );
}

export function daGrade(d: Date, timezone: string): Date {
  return new Date(
    new TZDate(
      d.getFullYear(),
      d.getMonth(),
      d.getDate(),
      d.getHours(),
      d.getMinutes(),
      d.getSeconds(),
      timezone,
    ).getTime(),
  );
}
