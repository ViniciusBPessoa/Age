/**
 * Escolha de tema em três estados, num segmentado à vista.
 *
 * `compacto` é o botão de um ícone só (barra lateral, tela de login),
 * que gira entre os três estados e diz no rótulo qual está valendo.
 */

"use client";

import { useEffect, useState } from "react";

import { IconMonitor, IconMoon, IconSun, cx } from "@/components/ui";
import { aplicarPreferencia, lerPreferencia, type PreferenciaDeTema } from "@/lib/tema";

const OPCOES: { valor: PreferenciaDeTema; rotulo: string; Icone: typeof IconSun }[] = [
  { valor: "sistema", rotulo: "Sistema", Icone: IconMonitor },
  { valor: "claro", rotulo: "Claro", Icone: IconSun },
  { valor: "escuro", rotulo: "Escuro", Icone: IconMoon },
];

function usePreferencia(): [PreferenciaDeTema | null, (p: PreferenciaDeTema) => void] {
  // `null` até o efeito: o servidor não sabe o tema (hidratação).
  const [pref, setPref] = useState<PreferenciaDeTema | null>(null);
  useEffect(() => {
    setPref(lerPreferencia());
    const ouvir = (e: Event) => setPref((e as CustomEvent<PreferenciaDeTema>).detail);
    globalThis.addEventListener("age-tema", ouvir);
    return () => globalThis.removeEventListener("age-tema", ouvir);
  }, []);
  return [pref, aplicarPreferencia];
}

export function SeletorDeTema({ className }: { className?: string }) {
  const [pref, escolher] = usePreferencia();
  return (
    <div
      role="radiogroup"
      aria-label="Tema"
      className={cx(
        "inline-grid grid-cols-3 gap-1 rounded-xl bg-surface-2 p-1 ring-1 ring-inset ring-line",
        className,
      )}
    >
      {OPCOES.map(({ valor, rotulo, Icone }) => {
        const ativo = pref === valor;
        return (
          <button
            key={valor}
            type="button"
            role="radio"
            aria-checked={ativo}
            onClick={() => escolher(valor)}
            className={cx(
              "flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors",
              "focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand",
              ativo ? "bg-surface text-ink shadow-raised" : "text-muted hover:text-ink",
            )}
          >
            <Icone className="h-3.5 w-3.5" />
            {rotulo}
          </button>
        );
      })}
    </div>
  );
}

export function BotaoDeTema({
  className,
  comTexto = false,
}: {
  className?: string;
  comTexto?: boolean;
}) {
  const [pref, escolher] = usePreferencia();
  const atual = OPCOES.find((o) => o.valor === pref) ?? OPCOES[0];
  const proximo = OPCOES[(OPCOES.indexOf(atual) + 1) % OPCOES.length];
  const Icone = atual.Icone;
  return (
    <button
      type="button"
      onClick={() => escolher(proximo.valor)}
      aria-label={`Tema: ${atual.rotulo}. Trocar para ${proximo.rotulo.toLowerCase()}.`}
      title={`Tema: ${atual.rotulo}`}
      className={cx(
        "flex items-center gap-3 rounded-lg text-muted transition-colors hover:bg-surface-2/60 hover:text-ink",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand",
        comTexto ? "w-full px-3 py-2 text-xs" : "h-9 w-9 justify-center",
        className,
      )}
    >
      <Icone className="h-[18px] w-[18px]" />
      {comTexto ? `Tema: ${atual.rotulo.toLowerCase()}` : null}
    </button>
  );
}
