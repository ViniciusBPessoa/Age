/**
 * O aviso de rodapé com "Desfazer", no padrão do Gmail e do Google Agenda.
 *
 * Arrastar um compromisso grava na hora (§8.0). O que torna isso seguro
 * não é perguntar antes, é poder voltar logo depois: o aviso diz o que
 * mudou e oferece desfazer por alguns segundos.
 */

"use client";

import { useEffect } from "react";

import { IconUndo, cx } from "@/components/ui";

export interface AvisoAgenda {
  id: number;
  texto: string;
  tom?: "normal" | "erro";
  desfazer?: () => void | Promise<void>;
}

export function Aviso({
  aviso,
  onSumir,
}: {
  aviso: AvisoAgenda | null;
  onSumir: () => void;
}) {
  useEffect(() => {
    if (!aviso) return;
    const t = setTimeout(onSumir, aviso.desfazer ? 7000 : 4500);
    return () => clearTimeout(t);
  }, [aviso, onSumir]);

  if (!aviso) return null;
  return (
    <div
      role="status"
      aria-live="polite"
      className={cx(
        "fixed bottom-[calc(5rem+env(safe-area-inset-bottom))] left-1/2 z-50 flex -translate-x-1/2 items-center gap-3 md:bottom-6",
        "max-w-[calc(100vw-2rem)] rounded-xl px-4 py-3 text-sm shadow-float animate-fade-up",
        aviso.tom === "erro" ? "bg-danger text-white" : "bg-ink text-canvas",
      )}
    >
      <span className="min-w-0">{aviso.texto}</span>
      {aviso.desfazer ? (
        <button
          type="button"
          onClick={() => {
            void aviso.desfazer?.();
            onSumir();
          }}
          className="flex shrink-0 items-center gap-1.5 rounded-lg px-2 py-1 font-semibold underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-current"
        >
          <IconUndo className="h-3.5 w-3.5" />
          Desfazer
        </button>
      ) : null}
    </div>
  );
}
