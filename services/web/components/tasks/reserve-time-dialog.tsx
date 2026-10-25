/**
 * O diálogo do botão "reservar tempo" (§8.1).
 *
 * ## O que mudou, e por quê
 *
 * A versão anterior **não deixava escolher horário**: pedia a duração e
 * devolvia uma lista vertical de sugestões do `Scheduler`. O relato foi
 * direto: *"esse menu tá uma porcaria (...) ele não permite escolher
 * minha própria hora caso eu queira. Ele só dá uma lista de horas. O
 * menu de lista de horas (...) é um pedaço de bosta."*
 *
 * A docstring antiga defendia a ausência do campo manual: *"quem digita
 * horário à mão escolhe sem saber que vai colidir com a aula das 16h, e
 * descobre no cartão de conflito depois"*. O argumento é legítimo — e
 * continua valendo. A resposta não é negar a escolha, é **avisar antes
 * de confirmar**: o campo manual existe, e quando o horário colide com
 * algo que já está na agenda a tela diz com o quê, antes do clique.
 *
 * ## A ordem da tela
 *
 * Data e hora **primeiro** (a escolha do usuário quando perguntei), as
 * sugestões embaixo como atalho. Quem já sabe quando quer estudar
 * preenche dois campos; quem não sabe clica numa sugestão e ela
 * preenche os mesmos campos — as duas vias terminam no mesmo lugar, em
 * vez de serem modos separados.
 *
 * ## O que NÃO mudou
 *
 * `GET /calendar/free-slots` é determinístico e não passa pelo LLM. O
 * resultado continua sendo um plano `proposed`: §5.8 vale para o clique
 * como vale para a conversa, e `validate_plan` no servidor continua
 * sendo a autoridade sobre conflito — o aviso daqui é cortesia, não
 * substituto.
 */

"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { addDays } from "date-fns";
import { useEffect, useMemo, useState } from "react";

import {
  Button,
  Dialog,
  Field,
  IconAlert,
  IconCheck,
  IconClock,
  Input,
  cx,
} from "@/components/ui";
import { RequestFailed, api } from "@/lib/api-client";
import { duration, range } from "@/lib/format";
import type { AgeEvent, Slot, Task } from "@/lib/types";

/** Durações que cobrem a maioria dos casos, em um clique. */
const ATALHOS = [30, 60, 90, 120];

/**
 * O próximo horário "redondo", para o campo não abrir em 14h07.
 *
 * Arredonda para cima em blocos de 30 min: ninguém reserva tempo para
 * começar no minuto em que clicou.
 */
function proximaHoraRedonda(): Date {
  const d = new Date();
  d.setSeconds(0, 0);
  d.setMinutes(d.getMinutes() > 30 ? 60 : 30);
  return d;
}

/** `YYYY-MM-DD` no fuso LOCAL (não `toISOString`, que converte para UTC). */
export function paraCampoData(d: Date): string {
  const mes = String(d.getMonth() + 1).padStart(2, "0");
  const dia = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${mes}-${dia}`;
}

/** `HH:MM` no fuso local. */
export function paraCampoHora(d: Date): string {
  const h = String(d.getHours()).padStart(2, "0");
  const m = String(d.getMinutes()).padStart(2, "0");
  return `${h}:${m}`;
}

/**
 * Junta data e hora dos campos num instante.
 *
 * `new Date("2026-10-26T14:00")` sem sufixo de fuso é interpretado
 * como horário LOCAL pelo navegador, que é o que a pessoa digitou.
 * Acrescentar `Z` faria 14h virar 11h em São Paulo.
 */
export function instanteDosCampos(data: string, hora: string): Date | null {
  if (!data || !hora) return null;
  const d = new Date(`${data}T${hora}`);
  return Number.isNaN(d.getTime()) ? null : d;
}

/**
 * Os compromissos que colidem com a janela escolhida.
 *
 * Sobreposição é `inicio < fimOutro && fim > inicioOutro`: encostar não
 * é colidir, senão reservar 15h-16h logo depois de um bloco que termina
 * às 15h daria conflito falso.
 */
export function conflitosCom(eventos: AgeEvent[], inicio: Date, fim: Date): AgeEvent[] {
  return eventos.filter((e) => {
    const ini = new Date(e.start);
    const f = new Date(e.end);
    return inicio < f && fim > ini;
  });
}

export function ReserveTimeDialog({
  task,
  timezone,
  onClose,
  onDone,
}: {
  task: Task;
  timezone: string;
  onClose: () => void;
  onDone: (planId: string) => void;
}) {
  // A estimativa da tarefa é o palpite inicial — é o número que a
  // pessoa já deu, e repetir a pergunta seria pedir duas vezes.
  const [minutes, setMinutes] = useState(String(task.estimated_min ?? 60));
  const [error, setError] = useState<string | null>(null);

  const inicial = useMemo(() => proximaHoraRedonda(), []);
  const [data, setData] = useState(() => paraCampoData(inicial));
  const [hora, setHora] = useState(() => paraCampoHora(inicial));

  const parsed = Number(minutes);
  const validDuration = Number.isFinite(parsed) && parsed >= 5 && parsed <= 600;

  const inicio = instanteDosCampos(data, hora);
  const fim =
    inicio && validDuration ? new Date(inicio.getTime() + parsed * 60_000) : null;

  const window_ = useMemo(() => {
    const now = new Date();
    const deadline = task.due_at
      ? new Date(task.due_at)
      : task.due_date
        ? new Date(`${task.due_date}T23:59:59`)
        : null;
    const end = deadline && deadline > now ? deadline : addDays(now, 14);
    return { start: now.toISOString(), end: end.toISOString(), deadline };
  }, [task.due_at, task.due_date]);

  const slots = useQuery({
    queryKey: ["free-slots", task.id, minutes],
    queryFn: () =>
      api.get<{ slots: Slot[]; hint: string | null }>("/calendar/free-slots", {
        start: window_.start,
        end: window_.end,
        duration_min: parsed,
        deadline: window_.deadline?.toISOString(),
        count: 6,
      }),
    enabled: validDuration,
  });

  /*
   * A agenda em volta do horário escolhido, só para avisar de colisão.
   *
   * Uma janela de um dia, não a janela inteira da busca: o aviso é
   * sobre o dia que a pessoa escolheu, e trazer duas semanas de eventos
   * para comparar com uma hora seria peso sem uso.
   */
  const diaEscolhido = inicio ? paraCampoData(inicio) : null;
  const agenda = useQuery({
    queryKey: ["events", "conflito-reserva", diaEscolhido],
    queryFn: () =>
      api.get<{ events: AgeEvent[] }>("/calendar/events", {
        start: `${diaEscolhido}T00:00:00`,
        end: `${diaEscolhido}T23:59:59`,
      }),
    enabled: diaEscolhido !== null,
    staleTime: 30_000,
  });

  const conflitos =
    inicio && fim ? conflitosCom(agenda.data?.events ?? [], inicio, fim) : [];

  // Limpa o erro do servidor quando a pessoa mexe nos campos: mantê-lo
  // faria a mensagem antiga descrever a escolha nova.
  useEffect(() => setError(null), [data, hora, minutes]);

  const reserve = useMutation({
    mutationFn: () =>
      api.post<{ plan_id: string; message: string }>(`/tasks/${task.id}/schedule`, {
        start: inicio?.toISOString(),
        end: fim?.toISOString(),
        title: task.title,
      }),
    onSuccess: (result) => onDone(result.plan_id),
    onError: (err) =>
      setError(
        err instanceof RequestFailed ? err.message : "Não consegui montar a reserva.",
      ),
  });

  function usarSugestao(slot: Slot) {
    const ini = new Date(slot.start);
    setData(paraCampoData(ini));
    setHora(paraCampoHora(ini));
    const dur = Math.round((new Date(slot.end).getTime() - ini.getTime()) / 60_000);
    if (dur > 0) setMinutes(String(dur));
  }

  const podeReservar = inicio !== null && fim !== null && validDuration;

  return (
    <Dialog
      open
      onClose={onClose}
      title={`Reservar tempo · ${task.title}`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button
            variant="primary"
            loading={reserve.isPending}
            disabled={!podeReservar}
            onClick={() => reserve.mutate()}
          >
            Reservar
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {/* 1. QUANTO TEMPO, com atalhos antes do campo livre. */}
        <Field
          label="Quanto tempo"
          htmlFor="res-min"
          error={validDuration ? error : "Use entre 5 e 600 minutos."}
        >
          <div className="space-y-2">
            <div className="flex flex-wrap gap-1.5">
              {ATALHOS.map((m) => (
                <button
                  key={m}
                  type="button"
                  aria-pressed={parsed === m}
                  onClick={() => setMinutes(String(m))}
                  className={cx(
                    "rounded-lg px-3 py-1.5 text-xs font-medium transition-colors",
                    parsed === m
                      ? "bg-brand text-brand-ink"
                      : "bg-surface-2 text-muted hover:bg-surface-3 hover:text-ink",
                  )}
                >
                  {duration(m)}
                </button>
              ))}
            </div>
            <Input
              id="res-min"
              type="number"
              min={5}
              max={600}
              value={minutes}
              onChange={(e) => setMinutes(e.target.value)}
              aria-label="Duração em minutos"
            />
          </div>
        </Field>

        {/* 2. QUANDO — o campo que faltava. */}
        <div className="grid grid-cols-2 gap-2.5">
          <Field label="Dia" htmlFor="res-data">
            <Input
              id="res-data"
              type="date"
              value={data}
              onChange={(e) => setData(e.target.value)}
            />
          </Field>
          <Field label="Hora" htmlFor="res-hora">
            <Input
              id="res-hora"
              type="time"
              value={hora}
              onChange={(e) => setHora(e.target.value)}
            />
          </Field>
        </div>

        {/*
         * O aviso de conflito, ANTES de confirmar.
         *
         * É o que permite ter o campo manual sem reintroduzir o
         * problema que a versão anterior evitava negando a escolha.
         * Não bloqueia: a pessoa pode querer dois compromissos no mesmo
         * horário, e quem decide é ela.
         */}
        {conflitos.length > 0 ? (
          <div
            role="status"
            aria-live="polite"
            className="rounded-xl bg-warn/10 px-3.5 py-3 text-xs leading-relaxed text-warn"
          >
            <span className="flex items-start gap-2">
              <IconAlert className="mt-px h-3.5 w-3.5 shrink-0" />
              <span>
                Esse horário colide com{" "}
                <strong className="font-medium">
                  {conflitos.map((c) => c.title).join(", ")}
                </strong>
                . Dá para reservar mesmo assim, mas os dois vão ficar no mesmo horário.
              </span>
            </span>
          </div>
        ) : null}

        {/* 3. SUGESTÕES, como atalho e não como única via. */}
        {validDuration ? (
          <div>
            <p className="eyebrow mb-2">
              Ou escolha um horário livre
              {window_.deadline ? " antes do prazo" : ""}
            </p>

            {slots.isLoading ? (
              <div className="space-y-1.5" aria-hidden>
                {Array.from({ length: 2 }).map((_, i) => (
                  <div key={i} className="skeleton h-11 rounded-xl" />
                ))}
              </div>
            ) : slots.data && slots.data.slots.length > 0 ? (
              <ul className="max-h-44 space-y-1.5 overflow-y-auto pr-0.5">
                {slots.data.slots.map((slot) => {
                  const ini = new Date(slot.start);
                  const igual =
                    paraCampoData(ini) === data && paraCampoHora(ini) === hora;
                  return (
                    <li key={`${slot.start}-${slot.end}`}>
                      <button
                        type="button"
                        onClick={() => usarSugestao(slot)}
                        aria-pressed={igual}
                        className={cx(
                          "flex w-full items-center gap-2.5 rounded-xl border px-3 py-2 text-left transition-all duration-150",
                          igual
                            ? "border-brand bg-brand/10"
                            : "border-line bg-surface-2 hover:border-line-strong hover:bg-surface-3",
                        )}
                      >
                        <IconClock
                          className={cx(
                            "h-3.5 w-3.5 shrink-0",
                            igual ? "text-brand" : "text-muted",
                          )}
                        />
                        <span className="min-w-0 flex-1">
                          <span className="tnum block text-xs font-medium">
                            {range(slot.start, slot.end, timezone)}
                          </span>
                          {slot.reasons.length > 0 ? (
                            <span className="mt-0.5 block text-2xs leading-relaxed text-muted">
                              {slot.reasons.join(" · ")}
                            </span>
                          ) : null}
                        </span>
                        {igual ? (
                          <span
                            aria-hidden
                            className="grid h-4 w-4 shrink-0 place-items-center rounded-full bg-brand text-brand-ink"
                          >
                            <IconCheck className="h-2.5 w-2.5" strokeWidth={3} />
                          </span>
                        ) : null}
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="rounded-xl bg-surface-2 px-3.5 py-2.5 text-xs leading-relaxed text-muted">
                {slots.data?.hint ??
                  "Não achei horário livre nessa janela — escolha a hora acima."}
              </p>
            )}
          </div>
        ) : null}

        <p className="rounded-xl bg-surface-2 px-3.5 py-3 text-xs leading-relaxed text-muted">
          Isto monta uma <strong className="font-medium text-ink-2">proposta</strong>: o
          bloco e a tarefa ficam ligados, e você aprova os dois juntos.{" "}
          {duration(parsed || 0)} sai da sua agenda só depois disso.
        </p>
      </div>
    </Dialog>
  );
}
