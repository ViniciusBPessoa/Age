/**
 * Shell das telas sem sessão: login e registro.
 *
 * Separado do shell do app porque não tem sidebar, nem navegação, nem
 * nada que exija estar autenticado — e porque colocar as duas coisas
 * num layout só levaria a `if (user)` espalhado pela árvore.
 */

import Link from "next/link";
import type { ReactNode } from "react";

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-md flex-col justify-center px-5 py-10">
      <header className="mb-8">
        <Link href="/" className="inline-flex items-center gap-2">
          <span
            aria-hidden
            className="grid h-8 w-8 place-items-center rounded-lg bg-[var(--accent)] text-sm font-bold text-[var(--accent-ink)]"
          >
            A
          </span>
          <span className="text-lg font-semibold">Age</span>
        </Link>
        <p className="mt-2 text-sm text-[var(--muted)]">
          Sua agenda, organizada por conversa.
        </p>
      </header>
      {children}
    </main>
  );
}
