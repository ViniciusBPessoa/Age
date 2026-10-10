/**
 * Shell das telas sem sessão: login e registro.
 *
 * Separado do shell do app porque não tem sidebar, nem navegação, nem
 * nada que exija estar autenticado — e porque colocar as duas coisas
 * num layout só levaria a `if (user)` espalhado pela árvore.
 *
 * É a primeira tela que alguém vê, e a versão anterior era um cartão
 * cinza num fundo preto chapado. Agora tem a aurora do `globals.css` e
 * uma frase que diz o que o produto faz — porque "Sua agenda,
 * organizada por conversa" sozinho não distingue isto de um app de
 * calendário qualquer.
 */

import Link from "next/link";
import type { ReactNode } from "react";

import { Logo } from "@/components/app-nav";

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="aurora flex min-h-dvh flex-col">
      <main className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center px-5 py-10">
        <header className="mb-8">
          <Link href="/" className="inline-flex items-center gap-2.5">
            <Logo />
            <span className="text-lg font-semibold tracking-[-0.02em]">Age</span>
          </Link>
          <h2 className="mt-5 text-2xl font-semibold leading-tight tracking-[-0.02em]">
            Sua agenda,
            <br />
            organizada por conversa.
          </h2>
          <p className="mt-2.5 text-sm leading-relaxed text-muted">
            Diga o que precisa fazer. O Age lê sua agenda, propõe um plano e espera você
            aprovar antes de escrever qualquer coisa.
          </p>
        </header>
        {children}
      </main>

      <footer className="px-5 pb-6 text-center text-2xs text-faint">
        Sua chave de LLM é sua. O Age não tem chave própria.
      </footer>
    </div>
  );
}
