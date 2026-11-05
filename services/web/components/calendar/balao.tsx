/**
 * Balão ancorado ao que foi clicado; folha de baixo no celular.
 *
 * É o padrão do Google Agenda para criar e ver um compromisso: o painel
 * abre AO LADO do bloco, e a grade continua visível atrás. Um modal no
 * meio da tela tapava justamente o contexto que a pessoa estava olhando
 * ("isso fica encostado na aula das 16h?").
 *
 * No celular vira folha que sobe de baixo (padrão do Samsung One UI e do
 * Material 3): o polegar alcança os botões, e não há espaço lateral para
 * ancorar nada numa tela de 390px.
 */

"use client";

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { IconX, cx } from "@/components/ui";
import type { Ancora } from "@/components/calendar-timeline/calendar-timeline";

const LARGURA = 360;
const MARGEM = 12;

export function Balao({
  ancora,
  aberto,
  onFechar,
  rotulo,
  children,
}: {
  ancora: Ancora | null;
  aberto: boolean;
  onFechar: () => void;
  /** Nome acessível do diálogo. */
  rotulo: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const [celular, setCelular] = useState(false);

  useEffect(() => {
    const mq = globalThis.matchMedia?.("(max-width: 640px)");
    if (!mq) return;
    const atualizar = () => setCelular(mq.matches);
    atualizar();
    mq.addEventListener("change", atualizar);
    return () => mq.removeEventListener("change", atualizar);
  }, []);

  /*
   * Posição: à direita do bloco se couber, senão à esquerda; na vertical,
   * alinhado ao topo do bloco e empurrado para dentro da janela. Medido
   * depois de pintar, porque a altura depende do conteúdo.
   */
  useLayoutEffect(() => {
    if (!aberto || celular || !ref.current) return;
    const alt = ref.current.offsetHeight;
    const w = globalThis.innerWidth;
    const h = globalThis.innerHeight;
    const a = ancora ?? { x: w / 2 - LARGURA / 2, y: h / 3 };
    const direita = a.x + (a.largura ?? 0) + MARGEM;
    const esquerda = a.x - LARGURA - MARGEM;
    let left =
      direita + LARGURA + MARGEM <= w
        ? direita
        : esquerda >= MARGEM
          ? esquerda
          : Math.max(MARGEM, Math.min(w - LARGURA - MARGEM, a.x));
    let top = Math.max(MARGEM, Math.min(h - alt - MARGEM, a.y));
    if (!Number.isFinite(left)) left = MARGEM;
    if (!Number.isFinite(top)) top = MARGEM;
    setPos({ left, top });
  }, [aberto, ancora, celular, children]);

  // Esc fecha; clique fora fecha. O foco vai para o primeiro campo.
  useEffect(() => {
    if (!aberto) return;
    const tecla = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onFechar();
      }
    };
    const fora = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onFechar();
    };
    document.addEventListener("keydown", tecla, true);
    // No próximo tique: o mesmo clique que abriu não pode fechar.
    const t = setTimeout(() => document.addEventListener("pointerdown", fora), 0);
    const foco = ref.current?.querySelector<HTMLElement>(
      "input, textarea, button:not([data-fechar])",
    );
    foco?.focus();
    return () => {
      document.removeEventListener("keydown", tecla, true);
      document.removeEventListener("pointerdown", fora);
      clearTimeout(t);
    };
  }, [aberto, onFechar]);

  if (!aberto) return null;

  return (
    <>
      {celular ? (
        <div aria-hidden className="fixed inset-0 z-40 bg-black/40 animate-fade-in" />
      ) : null}
      <div
        ref={ref}
        role="dialog"
        aria-modal="false"
        aria-label={rotulo}
        style={
          celular
            ? undefined
            : {
                left: pos?.left ?? -9999,
                top: pos?.top ?? -9999,
                width: LARGURA,
              }
        }
        className={cx(
          "z-50 border border-line-strong bg-surface text-ink shadow-float",
          celular
            ? "fixed inset-x-0 bottom-0 max-h-[85dvh] overflow-y-auto rounded-t-3xl px-5 pb-[max(1.25rem,env(safe-area-inset-bottom))] pt-3 animate-fade-up"
            : "fixed max-h-[calc(100dvh-24px)] overflow-y-auto rounded-2xl p-4 animate-scale-in",
        )}
      >
        {celular ? (
          <div
            aria-hidden
            className="mx-auto mb-3 h-1 w-10 rounded-full bg-line-strong"
          />
        ) : null}
        <button
          type="button"
          data-fechar
          onClick={onFechar}
          aria-label="Fechar"
          className="absolute right-3 top-3 grid h-8 w-8 place-items-center rounded-full text-muted transition-colors hover:bg-surface-2 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand"
        >
          <IconX className="h-4 w-4" />
        </button>
        {children}
      </div>
    </>
  );
}
