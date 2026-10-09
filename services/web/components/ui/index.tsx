/**
 * Primitivas de UI.
 *
 * Existem para que a decisão de acessibilidade seja tomada uma vez. §8.2
 * pede foco visível, ARIA nos cartões e navegação por teclado — se cada
 * tela montar o seu próprio botão, essa regra vira sorte. Aqui `<Button>`
 * já é `<button>` de verdade (foco e Enter de graça), `<Field>` já liga
 * `<label htmlFor>` ao id, e `<Dialog>` já prende o Escape.
 *
 * Deliberadamente sem biblioteca de componentes: o que esta UI precisa é
 * pequeno e específico, e trazer Radix ou shadcn custaria mais em
 * dependência do que estes ~200 linhas.
 */

"use client";

import {
  forwardRef,
  useEffect,
  useId,
  useRef,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";

// ---------------------------------------------------------------
// Botão
// ---------------------------------------------------------------
type Variant = "primary" | "ghost" | "danger" | "quiet";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-[var(--accent)] text-[var(--accent-ink)] hover:brightness-110",
  ghost:
    "border border-[var(--border-strong)] text-[var(--text)] hover:bg-[var(--panel-2)]",
  danger: "bg-[var(--danger)] text-white hover:brightness-110",
  quiet: "text-[var(--muted)] hover:text-[var(--text)] hover:bg-[var(--panel-2)]",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: "sm" | "md";
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "ghost", size = "md", loading, children, className = "", ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      // `disabled` enquanto carrega evita o duplo-envio, que no chat
      // custaria dois turnos de LLM (§5.1 trata idempotência no
      // backend; aqui é só não provocar).
      disabled={rest.disabled || loading}
      aria-busy={loading || undefined}
      className={[
        "inline-flex items-center justify-center gap-2 rounded-md font-medium",
        "transition-colors disabled:cursor-not-allowed disabled:opacity-40",
        size === "sm" ? "px-2.5 py-1 text-xs" : "px-3.5 py-2 text-sm",
        VARIANTS[variant],
        className,
      ].join(" ")}
      {...rest}
    >
      {loading ? <Spinner /> : null}
      {children}
    </button>
  );
});

export function Spinner({ className = "" }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={`inline-block h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent ${className}`}
    />
  );
}

// ---------------------------------------------------------------
// Campos
// ---------------------------------------------------------------
const FIELD_BASE =
  "w-full rounded-md border border-[var(--border-strong)] bg-[var(--bg)] px-3 py-2 " +
  "text-sm text-[var(--text)] placeholder:text-[var(--muted)] outline-none " +
  "focus:border-[var(--accent)] disabled:opacity-50";

export function Field({
  label,
  hint,
  error,
  children,
  htmlFor,
}: {
  label: string;
  hint?: ReactNode;
  error?: string | null;
  children: ReactNode;
  htmlFor?: string;
}) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="block text-sm font-medium">
        {label}
      </label>
      {children}
      {/* `role="alert"` para o leitor de tela anunciar o erro quando
          aparecer, em vez de a pessoa descobrir ao tabular de volta. */}
      {error ? (
        <p role="alert" className="text-xs text-[var(--danger)]">
          {error}
        </p>
      ) : hint ? (
        <p className="text-xs text-[var(--muted)]">{hint}</p>
      ) : null}
    </div>
  );
}

export const Input = forwardRef<
  HTMLInputElement,
  InputHTMLAttributes<HTMLInputElement>
>(function Input({ className = "", ...rest }, ref) {
  return <input ref={ref} className={`${FIELD_BASE} ${className}`} {...rest} />;
});

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement>
>(function Textarea({ className = "", ...rest }, ref) {
  return (
    <textarea
      ref={ref}
      className={`${FIELD_BASE} resize-none ${className}`}
      {...rest}
    />
  );
});

export const Select = forwardRef<
  HTMLSelectElement,
  SelectHTMLAttributes<HTMLSelectElement>
>(function Select({ className = "", ...rest }, ref) {
  return <select ref={ref} className={`${FIELD_BASE} ${className}`} {...rest} />;
});

// ---------------------------------------------------------------
// Superfícies
// ---------------------------------------------------------------
export function Panel({
  children,
  className = "",
  as: Tag = "section",
}: {
  children: ReactNode;
  className?: string;
  as?: "section" | "div" | "article" | "li";
}) {
  return (
    <Tag
      className={`rounded-lg border border-[var(--border)] bg-[var(--panel)] ${className}`}
    >
      {children}
    </Tag>
  );
}

export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "ok" | "warn" | "danger" | "accent" | "proposed";
}) {
  const tones: Record<string, string> = {
    neutral: "border-[var(--border-strong)] text-[var(--muted)]",
    ok: "border-[var(--ok)] text-[var(--ok)]",
    warn: "border-[var(--warn)] text-[var(--warn)]",
    danger: "border-[var(--danger)] text-[var(--danger)]",
    accent: "border-[var(--accent)] text-[var(--accent)]",
    proposed: "border-[var(--proposed)] text-[var(--proposed)]",
  };
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

/**
 * Estado vazio com uma ação, não só um texto triste.
 *
 * Lista vazia sem saída é um beco: a pessoa vê "nada aqui" e não sabe o
 * que fazer a respeito.
 */
export function Empty({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-3 px-6 py-12 text-center">
      <p className="text-sm font-medium">{title}</p>
      {children ? (
        <p className="max-w-sm text-xs leading-relaxed text-[var(--muted)]">
          {children}
        </p>
      ) : null}
      {action}
    </div>
  );
}

/**
 * Erro de carregamento com botão de tentar de novo (§5.9.13).
 *
 * "A conversa degrada" vale para a tela também: dizer o que falhou e
 * oferecer o retry é diferente de mostrar uma tela branca.
 */
export function LoadError({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      className="flex flex-col items-center gap-3 px-6 py-10 text-center"
    >
      <p className="text-sm text-[var(--danger)]">{message}</p>
      {onRetry ? (
        <Button size="sm" onClick={onRetry}>
          Tentar de novo
        </Button>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------
// Diálogo
// ---------------------------------------------------------------
/**
 * Modal sobre `<dialog>` nativo.
 *
 * O elemento nativo dá de graça o que costuma ser reimplementado errado:
 * Escape fecha, o foco fica preso dentro, e o resto da página fica
 * inerte para leitor de tela. Um `<div>` com `position: fixed` não tem
 * nada disso.
 */
export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    if (!open && el.open) el.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      // Clique no backdrop fecha. O `<dialog>` entrega o clique do
      // backdrop como clique no próprio elemento, daí a comparação.
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
      className="w-[min(32rem,92vw)] rounded-lg border border-[var(--border-strong)] bg-[var(--panel)] p-0 text-[var(--text)] backdrop:bg-black/60"
    >
      <div className="border-b border-[var(--border)] px-5 py-3">
        <h2 id={titleId} className="text-sm font-semibold">
          {title}
        </h2>
      </div>
      <div className="px-5 py-4 text-sm">{children}</div>
      {footer ? (
        <div className="flex justify-end gap-2 border-t border-[var(--border)] px-5 py-3">
          {footer}
        </div>
      ) : null}
    </dialog>
  );
}
