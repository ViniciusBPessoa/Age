/**
 * A metade esquerda do login: o que o Age faz, mostrado, não descrito.
 *
 * Telas de entrada de produtos grandes (Linear, Notion Calendar, Nubank
 * no web) mostram o próprio produto ao lado do formulário. Aqui é uma
 * miniatura da agenda com uma proposta esperando aprovação: a frase
 * "ele propõe, você aprova" vira imagem. Feita de HTML e tokens, sem
 * imagem: acompanha o tema e a paleta sozinha.
 */

import { IconCheck, IconLock, IconSparkle } from "@/components/ui";

const DIAS = [
  { d: "seg", n: 2 },
  { d: "ter", n: 3, hoje: true },
  { d: "qua", n: 4 },
];

export function Vitrine() {
  return (
    <div className="relative hidden overflow-hidden border-r border-line bg-surface lg:flex lg:flex-col lg:justify-between lg:p-12">
      <div
        aria-hidden
        className="pointer-events-none absolute -right-32 -top-32 h-96 w-96 rounded-full bg-brand/10 blur-3xl"
      />
      <div className="relative max-w-md">
        <p className="text-2xs font-semibold uppercase tracking-[0.14em] text-brand">
          Assistente de agenda
        </p>
        <h2 className="mt-3 text-3xl font-semibold leading-tight tracking-[-0.025em] text-ink [text-wrap:balance]">
          Diga o que precisa estudar. O Age encontra o tempo.
        </h2>
        <p className="mt-3 text-sm leading-relaxed text-muted">
          Ele lê sua agenda, quebra o assunto em sessões, encaixa nos horários livres e
          espera você aprovar antes de escrever qualquer coisa.
        </p>
      </div>

      <div aria-hidden className="relative mt-10 max-w-md space-y-3">
        <div className="rounded-2xl border border-line bg-canvas p-3 shadow-raised">
          <div className="grid grid-cols-[2.25rem_repeat(3,1fr)] text-center text-2xs text-muted">
            <span />
            {DIAS.map((x) => (
              <span key={x.d} className="pb-2">
                <span className={x.hoje ? "font-semibold text-brand" : ""}>{x.d}</span>
                <span
                  className={
                    x.hoje
                      ? "mx-auto mt-1 grid h-6 w-6 place-items-center rounded-full bg-brand text-xs font-bold text-brand-ink"
                      : "mt-1 block text-sm font-semibold text-ink"
                  }
                >
                  {x.n}
                </span>
              </span>
            ))}
          </div>
          <div className="relative grid h-40 grid-cols-[2.25rem_repeat(3,1fr)] border-t border-line">
            <div className="flex flex-col justify-between py-1 pr-1 text-right text-2xs tabular-nums text-faint">
              <span>08</span>
              <span>10</span>
              <span>12</span>
              <span>14</span>
            </div>
            {[0, 1, 2].map((c) => (
              <div key={c} className="relative border-l border-line" />
            ))}
            <div className="absolute left-[calc(2.25rem+2px)] top-1 h-14 w-[calc((100%-2.25rem)/3-6px)] rounded-md border-l-[3px] border-fixed bg-fixed/15 px-1.5 py-1 text-left text-2xs font-semibold text-ink">
              <span className="flex items-center gap-1">
                <IconLock className="h-2.5 w-2.5" />
                Cálculo II
              </span>
            </div>
            <div className="absolute left-[calc(2.25rem+(100%-2.25rem)/3+2px)] top-12 h-12 w-[calc((100%-2.25rem)/3-6px)] rounded-md border-l-[3px] border-flexible bg-flexible/15 px-1.5 py-1 text-left text-2xs font-semibold text-ink">
              Álgebra · sessão 2
            </div>
            <div className="age-hatch absolute left-[calc(2.25rem+2*(100%-2.25rem)/3+2px)] top-20 h-14 w-[calc((100%-2.25rem)/3-6px)] rounded-md border border-dashed border-proposed px-1.5 py-1 text-left text-2xs font-semibold text-proposed">
              Revisão: Matrizes
            </div>
          </div>
        </div>

        <div className="ml-8 rounded-2xl border border-line bg-canvas p-3.5 shadow-float">
          <p className="flex items-center gap-1.5 text-xs font-semibold text-ink">
            <span className="grid h-5 w-5 place-items-center rounded-md bg-brand text-brand-ink">
              <IconSparkle className="h-3 w-3" />
            </span>
            Age
          </p>
          <p className="mt-1.5 text-xs leading-relaxed text-ink-2">
            Achei espaço na quarta às 11h para revisar Matrizes, sem encostar na aula.
          </p>
          <div className="mt-2.5 flex items-center gap-2">
            <span className="inline-flex items-center gap-1 rounded-lg bg-brand px-2.5 py-1 text-2xs font-semibold text-brand-ink">
              <IconCheck className="h-3 w-3" />
              Aprovar
            </span>
            <span className="rounded-lg px-2.5 py-1 text-2xs font-semibold text-muted ring-1 ring-inset ring-line-strong">
              Ajustar
            </span>
          </div>
        </div>
      </div>

      <p className="relative mt-10 text-2xs text-faint">
        Nada entra na sua agenda sem você aprovar.
      </p>
    </div>
  );
}
