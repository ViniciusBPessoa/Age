/**
 * Criar e editar um compromisso à mão (§8.1).
 *
 * Dois relatos, um componente:
 *
 * - *"ele não permite a criação manual de horários. Se eu quisesse vir
 *   no meu calendário, eu mesmo marcar alguma coisa, eu não consigo."*
 * - *"não existe nenhum botão para fazer edição (...) a mesma coisa
 *   serve para um evento na agenda."*
 *
 * ## Por que o mesmo diálogo para os dois
 *
 * Criar e editar pedem os mesmos campos, e dois componentes quase
 * iguais divergem na primeira mudança — um ganha o campo de local e o
 * outro não. O modo muda o título, o verbo do botão e a rota; o resto
 * é idêntico.
 *
 * ## Por que escreve direto, sem proposta
 *
 * §5.8 ("nada entra na agenda sem aprovação") é sobre o que o MODELO
 * propõe: ele sugere, a pessoa decide. Uma edição que a própria pessoa
 * fez, num evento que ela está olhando, já é a decisão. É o mesmo
 * princípio que `POST /calendar/events` e `DELETE` já seguiam desde
 * antes.
 */

"use client";

import { TZDate } from "@date-fns/tz";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { SeletorDeCor, SeletorDeFlex } from "@/components/calendar/seletores";
import { Button, Dialog, Field, Input, Textarea } from "@/components/ui";
import { RequestFailed, api } from "@/lib/api-client";
import type { AgeEvent, EventColor, Flexibility } from "@/lib/types";

/**
 * A data como a pessoa a lê: no fuso da CONTA quando informado.
 *
 * Sem o fuso, os campos mostravam a hora do navegador, e quem abrisse a
 * agenda viajando veria "15:00" no diálogo de um evento que a grade
 * desenha às 14:00. A grade sempre usou o fuso da conta (§9.5).
 */
function noFuso(d: Date, timezone?: string): Date {
  return timezone ? new TZDate(d, timezone) : d;
}

/** `YYYY-MM-DD` no fuso da conta (ou local). `toISOString` daria UTC. */
export function campoData(d: Date, timezone?: string): string {
  const z = noFuso(d, timezone);
  const mes = String(z.getMonth() + 1).padStart(2, "0");
  const dia = String(z.getDate()).padStart(2, "0");
  return `${z.getFullYear()}-${mes}-${dia}`;
}

/** `HH:MM` no fuso da conta (ou local). */
export function campoHora(d: Date, timezone?: string): string {
  const z = noFuso(d, timezone);
  return `${String(z.getHours()).padStart(2, "0")}:${String(z.getMinutes()).padStart(
    2,
    "0",
  )}`;
}

/** Junta os campos num instante local (sem `Z`: a pessoa digita local). */
export function instante(data: string, hora: string, timezone?: string): Date | null {
  if (!data || !hora) return null;
  if (timezone) {
    const [a, m, d] = data.split("-").map(Number);
    const [h, min] = hora.split(":").map(Number);
    if ([a, m, d, h, min].some((n) => Number.isNaN(n))) return null;
    return new Date(new TZDate(a, m - 1, d, h, min, 0, timezone).getTime());
  }
  const d = new Date(`${data}T${hora}`);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function EventoDialog({
  evento,
  inicioSugerido,
  fimSugerido,
  onClose,
  onDone,
  timezone,
  tituloSugerido,
  corSugerida,
}: {
  /** Preenchido = editar; ausente = criar. */
  evento?: AgeEvent;
  /** Fuso da conta. Sem ele, os campos usam o do navegador. */
  timezone?: string;
  /** Vindo da criação rápida, quando a pessoa pede "mais opções". */
  tituloSugerido?: string;
  corSugerida?: EventColor | null;
  /** Para a criação por arrasto na grade, que já sabe o horário. */
  inicioSugerido?: Date;
  fimSugerido?: Date;
  onClose: () => void;
  onDone?: () => void;
}) {
  const editando = evento != null;
  const queryClient = useQueryClient();

  const ini = evento ? new Date(evento.start) : (inicioSugerido ?? new Date());
  const fim = evento
    ? new Date(evento.end)
    : (fimSugerido ?? new Date(ini.getTime() + 3600_000));

  const [titulo, setTitulo] = useState(evento?.title ?? tituloSugerido ?? "");
  const [data, setData] = useState(() => campoData(ini, timezone));
  const [horaIni, setHoraIni] = useState(() => campoHora(ini, timezone));
  const [horaFim, setHoraFim] = useState(() => campoHora(fim, timezone));
  const [cor, setCor] = useState<EventColor | null>(
    evento?.color ?? corSugerida ?? null,
  );
  const [local, setLocal] = useState(evento?.location ?? "");
  const [descricao, setDescricao] = useState(evento?.description ?? "");
  const [flex, setFlex] = useState<Flexibility>(evento?.flexibility ?? "fixed");
  const [erro, setErro] = useState<string | null>(null);

  const comeco = instante(data, horaIni, timezone);
  const termino = instante(data, horaFim, timezone);
  const horarioValido = comeco != null && termino != null && termino > comeco;
  const podeSalvar = titulo.trim().length > 0 && horarioValido;

  const salvar = useMutation({
    mutationFn: async () => {
      const corpo = {
        title: titulo.trim(),
        start: comeco?.toISOString(),
        end: termino?.toISOString(),
        location: local.trim() || null,
        description: descricao.trim() || null,
        flexibility: flex,
        color: cor,
      };
      return editando
        ? api.patch<AgeEvent>(`/calendar/events/${evento.id}`, corpo)
        : api.post<AgeEvent>("/calendar/events", corpo);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["events"] });
      onDone?.();
      onClose();
    },
    onError: (err) =>
      setErro(
        err instanceof RequestFailed
          ? err.message
          : "Não consegui salvar este compromisso.",
      ),
  });

  const apagar = useMutation({
    mutationFn: () => api.del(`/calendar/events/${evento?.id}`),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["events"] });
      onDone?.();
      onClose();
    },
    onError: (err) =>
      setErro(
        err instanceof RequestFailed
          ? err.message
          : "Não consegui apagar este compromisso.",
      ),
  });

  return (
    <Dialog
      open
      onClose={onClose}
      title={editando ? "Editar compromisso" : "Novo compromisso"}
      footer={
        <>
          {/*
           * Apagar à esquerda, longe do botão de salvar: é a ação
           * destrutiva, e vizinhança com o primário convida ao erro.
           */}
          {editando ? (
            <Button
              variant="ghost"
              loading={apagar.isPending}
              onClick={() => apagar.mutate()}
              className="mr-auto text-warn"
            >
              Apagar
            </Button>
          ) : null}
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button
            variant="primary"
            loading={salvar.isPending}
            disabled={!podeSalvar}
            onClick={() => salvar.mutate()}
          >
            {editando ? "Salvar" : "Criar"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="O quê" htmlFor="ev-titulo" error={erro}>
          <Input
            id="ev-titulo"
            value={titulo}
            autoFocus
            placeholder="Dentista, aula de cálculo, academia…"
            onChange={(e) => {
              setTitulo(e.target.value);
              setErro(null);
            }}
          />
        </Field>

        <Field label="Dia" htmlFor="ev-data">
          <Input
            id="ev-data"
            type="date"
            value={data}
            onChange={(e) => setData(e.target.value)}
          />
        </Field>

        <div className="grid grid-cols-2 gap-2.5">
          <Field label="Começa" htmlFor="ev-ini">
            <Input
              id="ev-ini"
              type="time"
              value={horaIni}
              onChange={(e) => setHoraIni(e.target.value)}
            />
          </Field>
          <Field
            label="Termina"
            htmlFor="ev-fim"
            error={
              comeco && termino && termino <= comeco
                ? "O fim precisa vir depois do começo."
                : undefined
            }
          >
            <Input
              id="ev-fim"
              type="time"
              value={horaFim}
              onChange={(e) => setHoraFim(e.target.value)}
            />
          </Field>
        </div>

        <Field
          label="Dá para remarcar?"
          htmlFor="ev-flex"
          hint="O Age só move o que você marcou como flexível."
        >
          <SeletorDeFlex id="ev-flex" valor={flex} onChange={setFlex} />
        </Field>

        <Field
          label="Cor"
          htmlFor="ev-cor"
          hint="Para separar aula, trabalho, saúde, lazer."
        >
          <SeletorDeCor id="ev-cor" valor={cor} onChange={setCor} />
        </Field>

        <Field label="Onde" htmlFor="ev-local" hint="Opcional.">
          <Input
            id="ev-local"
            value={local}
            onChange={(e) => setLocal(e.target.value)}
          />
        </Field>

        <Field label="Observações" htmlFor="ev-desc" hint="Opcional.">
          <Textarea
            id="ev-desc"
            rows={2}
            value={descricao}
            onChange={(e) => setDescricao(e.target.value)}
          />
        </Field>
      </div>
    </Dialog>
  );
}
