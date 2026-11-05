/**
 * Entrar e criar conta: vitrine à esquerda, formulário à direita.
 *
 * No celular a vitrine some e fica só o formulário, com a marca em
 * cima: a pessoa que abre o login no telefone quer entrar, não ler.
 * O tema pode ser trocado aqui mesmo, antes de ter conta.
 */

import Link from "next/link";
import type { ReactNode } from "react";

import { Logo } from "@/components/app-nav";
import { Vitrine } from "@/components/auth/vitrine";
import { BotaoDeTema } from "@/components/seletor-de-tema";

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="grid min-h-dvh bg-canvas lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]">
      <Vitrine />

      <div className="flex min-h-dvh flex-col">
        <div className="flex items-center justify-between px-5 pt-5 sm:px-8">
          <Link href="/" className="inline-flex items-center gap-2.5">
            <Logo />
            <span className="text-lg font-semibold tracking-[-0.02em]">Age</span>
          </Link>
          <BotaoDeTema />
        </div>

        <main className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center px-5 py-10">
          {children}
        </main>

        <footer className="px-5 pb-6 text-center text-2xs text-faint">
          Sua chave de LLM fica cifrada e só é usada nas suas conversas.
        </footer>
      </div>
    </div>
  );
}
