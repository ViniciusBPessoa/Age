/**
 * Navegação principal.
 *
 * Coluna à esquerda no desktop, barra inferior no mobile. A barra
 * inferior em vez de um menu hambúrguer porque as seis áreas são usadas
 * em sequência — marcar uma sessão, ver a agenda, voltar ao chat — e
 * esconder isso atrás de dois toques é atrito em cima do caminho mais
 * comum.
 *
 * O item ativo tem uma BARRA à esquerda, não só fundo mais claro. Fundo
 * sozinho é fraco demais em tema escuro: o olho não acha a página atual
 * numa varredura rápida, que é exatamente o que uma navegação deve
 * responder sem esforço.
 */

"use client";

import { BotaoDeTema } from "@/components/seletor-de-tema";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import {
  IconCalendar,
  IconChat,
  IconLogout,
  IconReport,
  IconRoutine,
  IconSettings,
  IconStudy,
  IconTasks,
  cx,
} from "@/components/ui";
import { auth } from "@/lib/api-client";
import { lerUltimaConversa } from "@/lib/ultimo-lugar";
import type { Me } from "@/lib/types";

const AREAS = [
  { href: "/chat", label: "Conversa", Icon: IconChat },
  { href: "/calendar", label: "Agenda", Icon: IconCalendar },
  { href: "/tasks", label: "Tarefas", Icon: IconTasks },
  { href: "/study", label: "Estudo", Icon: IconStudy },
  { href: "/routines", label: "Rotinas", Icon: IconRoutine },
  { href: "/report", label: "Relatório", Icon: IconReport },
] as const;

export function AppNav({ me }: { me: Me }) {
  const pathname = usePathname();
  const router = useRouter();

  const isActive = (href: string) =>
    pathname === href || pathname.startsWith(`${href}/`);

  /*
   * "Conversa" volta para a conversa que estava aberta (§8.1).
   *
   * Antes o `href` era `/chat` fixo, então sair para a agenda e voltar
   * sempre caía na tela vazia: a conversa continuava existindo na
   * lista, mas era preciso achá-la e clicar de novo.
   *
   * Lido num efeito e guardado em estado porque `localStorage` não
   * existe no servidor, e usá-lo direto no `href` durante a
   * renderização daria divergência de hidratação.
   */
  const [ultimaConversa, setUltimaConversa] = useState<string | null>(null);

  useEffect(() => {
    setUltimaConversa(lerUltimaConversa());
    // Reage à navegação: ao voltar da agenda o valor guardado pode ter
    // mudado, e um array de dependências vazio congelaria o primeiro.
  }, [pathname]);

  const destino = (href: string) =>
    href === "/chat" && ultimaConversa ? `/chat/${ultimaConversa}` : href;

  async function logout() {
    await auth.logout().catch(() => undefined);
    router.replace("/login");
  }

  const initial = (me.name ?? me.email).trim().charAt(0).toUpperCase() || "?";

  return (
    <>
      {/* ---------------- Desktop ---------------- */}
      <nav
        aria-label="Áreas do Age"
        className="hidden w-60 shrink-0 flex-col border-r border-line bg-surface px-3 py-5 md:flex"
      >
        <Link
          href="/chat"
          className="mb-6 flex items-center gap-2.5 rounded-lg px-2 py-1"
        >
          <Logo />
          <span className="text-base font-semibold tracking-[-0.02em]">Age</span>
        </Link>

        <ul className="flex-1 space-y-1">
          {AREAS.map(({ href, label, Icon }) => {
            const active = isActive(href);
            return (
              <li key={href} className="relative">
                {active ? (
                  <span
                    aria-hidden
                    className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-brand"
                  />
                ) : null}
                <Link
                  href={destino(href)}
                  aria-current={active ? "page" : undefined}
                  className={cx(
                    "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors duration-150",
                    active
                      ? "bg-surface-2 font-medium text-ink"
                      : "text-muted hover:bg-surface-2/60 hover:text-ink",
                  )}
                >
                  <Icon className={cx("h-[18px] w-[18px]", active && "text-brand")} />
                  {label}
                </Link>
              </li>
            );
          })}
        </ul>

        <div className="mt-4 space-y-1 border-t border-line pt-3">
          <ThemeToggle />
          <Link
            href="/settings"
            aria-current={isActive("/settings") ? "page" : undefined}
            className={cx(
              "flex items-center gap-2.5 rounded-lg px-2.5 py-2 transition-colors",
              isActive("/settings")
                ? "bg-surface-2 text-ink"
                : "text-muted hover:bg-surface-2/60 hover:text-ink",
            )}
          >
            <span
              aria-hidden
              className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-brand-soft text-xs font-semibold text-ink"
            >
              {initial}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-xs font-medium text-ink">
                {me.name ?? me.email.split("@")[0]}
              </span>
              <span className="block truncate text-2xs normal-case tracking-normal text-muted">
                Configurações
              </span>
            </span>
            <IconSettings className="h-3.5 w-3.5 opacity-60" />
          </Link>
          <button
            onClick={logout}
            className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-xs text-muted transition-colors hover:bg-surface-2/60 hover:text-ink"
          >
            <IconLogout className="h-[18px] w-[18px]" />
            Sair
          </button>
        </div>
      </nav>

      {/* ---------------- Mobile ----------------
          Barra inferior fixa. `pb-[env(safe-area-inset-bottom)]` para
          não ficar debaixo da barra de gestos do iOS. */}
      <nav
        aria-label="Áreas do Age"
        className="glass fixed bottom-0 left-0 right-0 z-40 flex border-t border-line pb-[env(safe-area-inset-bottom)] md:hidden"
      >
        {AREAS.map(({ href, label, Icon }) => {
          const active = isActive(href);
          return (
            <Link
              key={href}
              href={destino(href)}
              aria-current={active ? "page" : undefined}
              className={cx(
                "flex flex-1 flex-col items-center gap-1 pb-2 pt-2.5 text-[10px] font-medium transition-colors",
                active ? "text-brand" : "text-muted",
              )}
            >
              {/* O fundo tingido no ativo dá área de toque visível, que
                  é o que falta numa barra só de cor de texto. */}
              <span
                className={cx(
                  "grid h-7 w-9 place-items-center rounded-lg transition-colors",
                  active && "bg-brand/12",
                )}
              >
                <Icon className="h-[18px] w-[18px]" />
              </span>
              {label}
            </Link>
          );
        })}
        <Link
          href="/settings"
          aria-current={isActive("/settings") ? "page" : undefined}
          className={cx(
            "flex flex-1 flex-col items-center gap-1 pb-2 pt-2.5 text-[10px] font-medium transition-colors",
            isActive("/settings") ? "text-brand" : "text-muted",
          )}
        >
          <span
            className={cx(
              "grid h-7 w-9 place-items-center rounded-lg transition-colors",
              isActive("/settings") && "bg-brand/12",
            )}
          >
            <IconSettings className="h-[18px] w-[18px]" />
          </span>
          Ajustes
        </Link>
      </nav>
    </>
  );
}

export function Logo({ className }: { className?: string }) {
  /*
   * A marca: o "A" do Age desenhado como um pico, sobre a cor da marca,
   * o mesmo desenho do ícone da aba (`public/icon.svg`). Antes era a
   * letra em branco sobre um gradiente azul-roxo, que com a paleta Céu
   * escura (azul claro) ficava com contraste baixo. `brand-ink` é a cor
   * calculada para ler sobre `brand` nos dois temas.
   */
  return (
    <span
      aria-hidden
      className={cx(
        "grid h-8 w-8 shrink-0 place-items-center rounded-[10px] bg-brand text-brand-ink shadow-brand",
        className,
      )}
    >
      <svg viewBox="0 0 32 32" className="h-5 w-5" fill="none">
        <path
          d="M9 23 16 9l7 14"
          stroke="currentColor"
          strokeWidth="3"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <path
          d="M11.8 18h8.4"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
        />
      </svg>
    </span>
  );
}

/**
 * Alternador de tema: agora em três estados (sistema, claro, escuro),
 * em `components/seletor-de-tema.tsx`. Este nome fica para quem já o
 * usava aqui.
 */
function ThemeToggle() {
  return <BotaoDeTema comTexto />;
}
