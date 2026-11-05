/**
 * Dois seletores da agenda: cor de categoria e flexibilidade.
 *
 * Os dois eram `<select>` nativos. Trocar por botões visíveis segue o
 * Google Agenda (as bolinhas de cor) e o segmentado do Material 3: as
 * opções ficam à vista, a escolha atual aparece sem abrir nada, e no
 * celular cada alvo tem 32px ou mais.
 */

"use client";

import { IconCheck, IconLock, IconUnlock, cx } from "@/components/ui";
import { CORES_DE_EVENTO, nomeDaCor } from "@/lib/cores-de-evento";
import type { EventColor, Flexibility } from "@/lib/types";

export function SeletorDeCor({
  valor,
  onChange,
  id,
}: {
  valor: EventColor | null;
  onChange: (cor: EventColor | null) => void;
  id?: string;
}) {
  return (
    <div
      id={id}
      role="radiogroup"
      aria-label={`Cor do compromisso: ${nomeDaCor(valor)}`}
      className="flex flex-wrap items-center gap-1.5"
    >
      <button
        type="button"
        role="radio"
        aria-checked={valor === null}
        aria-label="Cor do tipo (sem categoria)"
        title="Cor do tipo"
        onClick={() => onChange(null)}
        className={cx(
          "grid h-7 w-7 place-items-center rounded-full border border-dashed border-line-strong text-muted transition-transform",
          "hover:scale-110 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand",
          valor === null && "ring-2 ring-ink ring-offset-2 ring-offset-surface",
        )}
      >
        {valor === null ? <IconCheck className="h-3.5 w-3.5" /> : null}
      </button>
      {CORES_DE_EVENTO.map((cor) => (
        <button
          key={cor.valor}
          type="button"
          role="radio"
          aria-checked={valor === cor.valor}
          aria-label={cor.nome}
          title={cor.nome}
          onClick={() => onChange(cor.valor)}
          style={{ background: `rgb(var(--ev-${cor.valor}))` }}
          className={cx(
            "grid h-7 w-7 place-items-center rounded-full text-white transition-transform",
            "hover:scale-110 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand",
            valor === cor.valor && "ring-2 ring-ink ring-offset-2 ring-offset-surface",
          )}
        >
          {valor === cor.valor ? (
            <IconCheck className="h-3.5 w-3.5 drop-shadow-[0_1px_1px_rgb(0_0_0/0.5)]" />
          ) : null}
        </button>
      ))}
    </div>
  );
}

const FLEX: { valor: Flexibility; rotulo: string; dica: string }[] = [
  { valor: "fixed", rotulo: "Fixo", dica: "O Age nunca move" },
  { valor: "semi_flexible", rotulo: "Semi", dica: "Move avisando" },
  { valor: "flexible", rotulo: "Flexível", dica: "Pode remarcar" },
];

export function SeletorDeFlex({
  valor,
  onChange,
  id,
}: {
  valor: Flexibility;
  onChange: (f: Flexibility) => void;
  id?: string;
}) {
  return (
    <div
      id={id}
      role="radiogroup"
      aria-label="O Age pode mover este compromisso?"
      className="grid grid-cols-3 gap-1 rounded-xl bg-surface-2 p-1 ring-1 ring-inset ring-line"
    >
      {FLEX.map((f) => {
        const ativo = valor === f.valor;
        return (
          <button
            key={f.valor}
            type="button"
            role="radio"
            aria-checked={ativo}
            onClick={() => onChange(f.valor)}
            className={cx(
              "flex flex-col items-center gap-0.5 rounded-lg px-2 py-1.5 text-xs font-semibold transition-colors",
              "focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand",
              ativo ? "bg-surface text-ink shadow-raised" : "text-muted hover:text-ink",
            )}
          >
            <span className="flex items-center gap-1">
              {f.valor === "fixed" ? (
                <IconLock className="h-3 w-3" />
              ) : (
                <IconUnlock className="h-3 w-3" />
              )}
              {f.rotulo}
            </span>
            <span className="text-2xs font-normal text-muted">{f.dica}</span>
          </button>
        );
      })}
    </div>
  );
}
