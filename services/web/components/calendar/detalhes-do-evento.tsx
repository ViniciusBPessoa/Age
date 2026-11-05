/**
 * O balão de um compromisso: o que é, quando, e as ações à mão.
 *
 * Substitui o modal que abria no meio da tela. As ações seguem a ordem
 * do Google Agenda (editar, duplicar, apagar) e mantêm a regra antiga
 * desta tela: a destrutiva fica longe da comum. Cor e flexibilidade
 * mudam com um toque, sem abrir o formulário.
 */

"use client";

import { format } from "date-fns";
import { ptBR } from "date-fns/locale";
import { TZDate } from "@date-fns/tz";

import { SeletorDeCor, SeletorDeFlex } from "@/components/calendar/seletores";
import {
  Badge,
  Button,
  IconClock,
  IconEdit,
  IconPin,
  IconSparkle,
  IconTrash,
} from "@/components/ui";
import type { AgeEvent, EventColor, Flexibility } from "@/lib/types";

export function DetalhesDoEvento({
  evento,
  timezone,
  ocupado,
  onEditar,
  onDuplicar,
  onApagar,
  onCor,
  onFlex,
}: {
  evento: AgeEvent;
  timezone: string;
  ocupado: boolean;
  onEditar: () => void;
  onDuplicar: () => void;
  onApagar: () => void;
  onCor: (cor: EventColor | null) => void;
  onFlex: (f: Flexibility) => void;
}) {
  const zi = new TZDate(new Date(evento.start), timezone);
  const zf = new TZDate(new Date(evento.end), timezone);
  const quando = evento.all_day
    ? format(zi, "EEEE, d 'de' MMMM", { locale: ptBR })
    : `${format(zi, "EEEE, d 'de' MMMM", { locale: ptBR })} · ${format(zi, "HH:mm")} – ${format(
        zf,
        "HH:mm",
      )}`;

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3 pr-9">
        <span
          aria-hidden
          className="mt-1.5 h-3.5 w-3.5 shrink-0 rounded"
          style={{
            background: evento.color
              ? `rgb(var(--ev-${evento.color}))`
              : evento.flexibility === "fixed"
                ? "rgb(var(--fixed))"
                : evento.flexibility === "semi_flexible"
                  ? "rgb(var(--warn))"
                  : "rgb(var(--flexible))",
          }}
        />
        <div className="min-w-0">
          <h2 className="text-lg font-semibold leading-snug [overflow-wrap:anywhere]">
            {evento.title}
          </h2>
          <p className="mt-1 flex items-center gap-1.5 text-sm text-ink-2">
            <IconClock className="h-3.5 w-3.5 shrink-0 text-muted" />
            <span className="first-letter:uppercase">{quando}</span>
          </p>
          {evento.location ? (
            <p className="mt-1 flex items-center gap-1.5 text-sm text-muted">
              <IconPin className="h-3.5 w-3.5 shrink-0" />
              {evento.location}
            </p>
          ) : null}
          {evento.created_by_age ? (
            <div className="mt-2">
              <Badge tone="brand">
                <IconSparkle className="h-2.5 w-2.5" />
                criado pelo Age
              </Badge>
            </div>
          ) : null}
        </div>
      </div>

      {evento.description ? (
        <p className="whitespace-pre-wrap rounded-xl bg-surface-2 px-3 py-2.5 text-sm leading-relaxed text-ink-2">
          {evento.description}
        </p>
      ) : null}

      <div className="space-y-1.5">
        <p className="text-2xs font-semibold uppercase tracking-wider text-muted">
          O Age pode mover?
        </p>
        <SeletorDeFlex valor={evento.flexibility} onChange={onFlex} />
      </div>

      <div className="space-y-1.5">
        <p className="text-2xs font-semibold uppercase tracking-wider text-muted">
          Cor
        </p>
        <SeletorDeCor valor={evento.color ?? null} onChange={onCor} />
      </div>

      <div className="flex items-center gap-2 border-t border-line pt-3">
        <Button variant="danger" size="sm" onClick={onApagar} disabled={ocupado}>
          <IconTrash className="h-3.5 w-3.5" />
          Apagar
        </Button>
        <span className="flex-1" />
        <Button variant="ghost" size="sm" onClick={onDuplicar} disabled={ocupado}>
          Duplicar
        </Button>
        <Button variant="secondary" size="sm" onClick={onEditar} disabled={ocupado}>
          <IconEdit className="h-3.5 w-3.5" />
          Editar
        </Button>
      </div>
    </div>
  );
}
