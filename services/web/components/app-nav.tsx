/**
 * Navegação principal.
 *
 * Coluna à esquerda no desktop, barra inferior no mobile. A barra
 * inferior em vez de um menu hambúrguer porque as cinco áreas são
 * usadas em sequência — marcar uma sessão, ver a agenda, voltar ao chat
 * — e esconder isso atrás de dois toques é atrito em cima do caminho
 * mais comum.
 */

"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

import { auth } from "@/lib/api-client";
import type { Me } from "@/lib/types";

const AREAS = [
  { href: "/chat", label: "Conversa", icon: "💬" },
  { href: "/calendar", label: "Agenda", icon: "📅" },
  { href: "/tasks", label: "Tarefas", icon: "📋" },
  { href: "/study", label: "Estudo", icon: "📚" },
  { href: "/routines", label: "Rotinas", icon: "🔁" },
  { href: "/report", label: "Relatório", icon: "📊" },
] as const;

export function AppNav({ me }: { me: Me }) {
  const pathname = usePathname();
  const router = useRouter();

  const isActive = (href: string) =>
    pathname === href || pathname.startsWith(`${href}/`);

  async function logout() {
    await auth.logout().catch(() => undefined);
    router.replace("/login");
  }

  return (
    <>
      {/* Desktop */}
      <nav
        aria-label="Áreas do Age"
        className="hidden w-52 shrink-0 flex-col border-r border-[var(--border)] bg-[var(--panel)] px-3 py-4 md:flex"
      >
        <Link href="/chat" className="mb-5 flex items-center gap-2 px-2">
          <span
            aria-hidden
            className="grid h-7 w-7 place-items-center rounded-lg bg-[var(--accent)] text-xs font-bold text-[var(--accent-ink)]"
          >
            A
          </span>
          <span className="text-sm font-semibold">Age</span>
        </Link>

        <ul className="flex-1 space-y-0.5">
          {AREAS.map((area) => (
            <li key={area.href}>
              <Link
                href={area.href}
                aria-current={isActive(area.href) ? "page" : undefined}
                className={[
                  "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors",
                  isActive(area.href)
                    ? "bg-[var(--panel-2)] text-[var(--text)]"
                    : "text-[var(--muted)] hover:bg-[var(--panel-2)] hover:text-[var(--text)]",
                ].join(" ")}
              >
                <span aria-hidden>{area.icon}</span>
                {area.label}
              </Link>
            </li>
          ))}
        </ul>

        <div className="mt-4 border-t border-[var(--border)] pt-3">
          <Link
            href="/settings"
            aria-current={isActive("/settings") ? "page" : undefined}
            className={[
              "block truncate rounded-md px-2.5 py-2 text-xs transition-colors",
              isActive("/settings")
                ? "bg-[var(--panel-2)] text-[var(--text)]"
                : "text-[var(--muted)] hover:bg-[var(--panel-2)] hover:text-[var(--text)]",
            ].join(" ")}
          >
            ⚙ {me.name ?? me.email}
          </Link>
          <button
            onClick={logout}
            className="mt-0.5 block w-full rounded-md px-2.5 py-2 text-left text-xs text-[var(--muted)] transition-colors hover:bg-[var(--panel-2)] hover:text-[var(--text)]"
          >
            Sair
          </button>
        </div>
      </nav>

      {/* Mobile: barra inferior, fixa. `pb-[env(safe-area-inset-bottom)]`
          para não ficar debaixo da barra de gestos do iOS. */}
      <nav
        aria-label="Áreas do Age"
        className="fixed bottom-0 left-0 right-0 z-40 flex border-t border-[var(--border)] bg-[var(--panel)] pb-[env(safe-area-inset-bottom)] md:hidden"
      >
        {AREAS.map((area) => (
          <Link
            key={area.href}
            href={area.href}
            aria-current={isActive(area.href) ? "page" : undefined}
            className={[
              "flex flex-1 flex-col items-center gap-0.5 py-2 text-[10px]",
              isActive(area.href) ? "text-[var(--accent)]" : "text-[var(--muted)]",
            ].join(" ")}
          >
            <span aria-hidden className="text-base">
              {area.icon}
            </span>
            {area.label}
          </Link>
        ))}
        <Link
          href="/settings"
          aria-current={isActive("/settings") ? "page" : undefined}
          className={[
            "flex flex-1 flex-col items-center gap-0.5 py-2 text-[10px]",
            isActive("/settings") ? "text-[var(--accent)]" : "text-[var(--muted)]",
          ].join(" ")}
        >
          <span aria-hidden className="text-base">
            ⚙
          </span>
          Ajustes
        </Link>
      </nav>
    </>
  );
}
