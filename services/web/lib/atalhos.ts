/**
 * Atalhos de teclado da agenda, no espírito do Google Agenda e do
 * Notion Calendar: uma tecla, sem modificador, ignorada enquanto a
 * pessoa digita num campo.
 */

import { useEffect } from "react";

export const ATALHOS_DA_AGENDA: { teclas: string; o_que: string }[] = [
  { teclas: "N", o_que: "novo compromisso" },
  { teclas: "T", o_que: "voltar para hoje" },
  { teclas: "D · S · M · L", o_que: "dia, semana, mês, lista" },
  { teclas: "← →  ou  J K", o_que: "período anterior e seguinte" },
  { teclas: "?", o_que: "mostrar esta ajuda" },
];

function digitando(alvo: EventTarget | null): boolean {
  const el = alvo as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return (
    tag === "INPUT" ||
    tag === "TEXTAREA" ||
    tag === "SELECT" ||
    el.isContentEditable === true
  );
}

export function useAtalhos(mapa: Record<string, () => void>, ativo = true): void {
  useEffect(() => {
    if (!ativo) return;
    const ouvir = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (digitando(e.target)) return;
      // Um diálogo aberto tem os próprios atalhos (Esc, Enter).
      if (document.querySelector("dialog[open], [role='dialog']")) return;
      const tecla = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      const acao = mapa[tecla];
      if (acao) {
        e.preventDefault();
        acao();
      }
    };
    globalThis.addEventListener("keydown", ouvir);
    return () => globalThis.removeEventListener("keydown", ouvir);
  }, [mapa, ativo]);
}
