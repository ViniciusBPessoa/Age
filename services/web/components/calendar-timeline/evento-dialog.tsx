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

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Button, Dialog, Field, Input, Select, Textarea } from "@/components/ui";
import { RequestFailed, api } from "@/lib/api-client";
import type { AgeEvent, Flexibility } from "@/lib/types";

const FLEX_LABEL: Record<Flexibility, string> = {
  fixed: "Fixo · não pode mover",
  semi_flexible: "Semi-flexível · dá para ajustar",
  flexible: "Flexível · pode remarcar",
};

/** `YYYY-MM-DD` no fuso LOCAL. `toISOString` converteria para UTC. */
export function campoData(d: Date): string {
  const mes = String(d.getMonth() + 1).padStart(2, "0");
  const dia = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${mes}-${dia}`;
}

/** `HH:MM` no fuso local. */
export function campoHora(d: Date): string {
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(
    2,
    "0",
  )}`;
}

/** Junta os campos num instante local (sem `Z`: a pessoa digita local). */
export function instante(data: string, hora: string): Date | null {
  if (!data || !hora) return null;
  const d = new Date(`${data}T${hora}`);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function EventoDialog({
  evento,
  inicioSugerido,
  fimSugerido,
  onClose,
  onDone,
}: {
  /** Preenchido = editar; ausente = criar. */
  evento?: AgeEvent;
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

  const [titulo, setTitulo] = useState(evento?.title ?? "");
  const [data, setData] = useState(() => campoData(ini));
  const [horaIni, setHoraIni] = useState(() => campoHora(ini));
  const [horaFim, setHoraFim] = useState(() => campoHora(fim));
  const [local, setLocal] = useState(evento?.location ?? "");
  const [descricao, setDescricao] = useState(evento?.description ?? "");
  const [flex, setFlex] = useState<Flexibility>(evento?.flexibility ?? "fixed");
  const [erro, setErro] = useState<string | null>(null);

  const comeco = instante(data, horaIni);
  const termino = instante(data, horaFim);
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
          <Select
            id="ev-flex"
            value={flex}
            onChange={(e) => setFlex(e.target.value as Flexibility)}
          >
            {(Object.keys(FLEX_LABEL) as Flexibility[]).map((f) => (
              <option key={f} value={f}>
                {FLEX_LABEL[f]}
              </option>
            ))}
          </Select>
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
