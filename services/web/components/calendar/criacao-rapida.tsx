/**
 * Criação rápida: título, cor, Enter. O resto fica em "Mais opções".
 *
 * É o balão do Google Agenda ao clicar num horário vazio. A maior parte
 * dos compromissos marcados à mão é "dentista quinta 15h": o horário já
 * veio do clique, falta só o nome. Abrir o formulário inteiro para isso
 * era o atrito que fazia a pessoa voltar para o chat.
 */

"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import { ptBR } from "date-fns/locale";
import { TZDate } from "@date-fns/tz";
import { useState } from "react";

import { SeletorDeCor, SeletorDeFlex } from "@/components/calendar/seletores";
import { Button, IconClock, Input } from "@/components/ui";
import { RequestFailed, api } from "@/lib/api-client";
import type { AgeEvent, EventColor, Flexibility } from "@/lib/types";

export function CriacaoRapida({
  inicio,
  fim,
  timezone,
  onCriado,
  onMaisOpcoes,
}: {
  inicio: Date;
  fim: Date;
  timezone: string;
  onCriado: (ev: AgeEvent) => void;
  onMaisOpcoes: (titulo: string, cor: EventColor | null) => void;
}) {
  const queryClient = useQueryClient();
  const [titulo, setTitulo] = useState("");
  const [cor, setCor] = useState<EventColor | null>(null);
  const [flex, setFlex] = useState<Flexibility>("fixed");
  const [erro, setErro] = useState<string | null>(null);

  const zi = new TZDate(inicio, timezone);
  const zf = new TZDate(fim, timezone);
  const quando = `${format(zi, "EEEE, d 'de' MMMM", { locale: ptBR })} · ${format(
    zi,
    "HH:mm",
  )} – ${format(zf, "HH:mm")}`;

  const criar = useMutation({
    mutationFn: () =>
      api.post<AgeEvent>("/calendar/events", {
        title: titulo.trim(),
        start: inicio.toISOString(),
        end: fim.toISOString(),
        flexibility: flex,
        color: cor,
      }),
    onSuccess: async (ev) => {
      await queryClient.invalidateQueries({ queryKey: ["events"] });
      onCriado(ev);
    },
    onError: (err) =>
      setErro(err instanceof RequestFailed ? err.message : "Não consegui criar."),
  });

  return (
    <form
      className="space-y-3.5"
      onSubmit={(e) => {
        e.preventDefault();
        if (titulo.trim()) criar.mutate();
      }}
    >
      <div className="pr-9">
        <label htmlFor="rapido-titulo" className="sr-only">
          Título do compromisso
        </label>
        <Input
          id="rapido-titulo"
          value={titulo}
          onChange={(e) => {
            setTitulo(e.target.value);
            setErro(null);
          }}
          placeholder="Adicionar título"
          autoComplete="off"
          className="border-0 border-b border-line-strong bg-transparent px-0 text-lg font-semibold focus:border-brand"
        />
      </div>
      <p className="flex items-center gap-2 text-sm text-ink-2 first-letter:uppercase">
        <IconClock className="h-4 w-4 shrink-0 text-muted" />
        <span className="first-letter:uppercase">{quando}</span>
      </p>
      <SeletorDeFlex valor={flex} onChange={setFlex} />
      <SeletorDeCor valor={cor} onChange={setCor} />
      {erro ? (
        <p role="alert" className="text-xs text-danger">
          {erro}
        </p>
      ) : null}
      <div className="flex items-center justify-between gap-2 pt-1">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => onMaisOpcoes(titulo, cor)}
        >
          Mais opções
        </Button>
        <Button
          type="submit"
          variant="primary"
          size="sm"
          loading={criar.isPending}
          disabled={!titulo.trim()}
        >
          Salvar
        </Button>
      </div>
    </form>
  );
}
