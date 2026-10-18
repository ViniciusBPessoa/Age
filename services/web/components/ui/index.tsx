/**
 * Primitivas de UI.
 *
 * Existem para que a decisão de acessibilidade seja tomada uma vez.
 * §8.2 pede foco visível, ARIA nos cartões e navegação por teclado — se
 * cada tela montar o seu próprio botão, essa regra vira sorte. Aqui
 * `<Button>` já é `<button>` de verdade (foco e Enter de graça),
 * `<Field>` já liga `<label htmlFor>` ao id, e `<Dialog>` já prende o
 * Escape.
 *
 * Deliberadamente sem biblioteca de componentes: o que esta UI precisa
 * é pequeno e específico, e trazer Radix ou shadcn custaria mais em
 * dependência do que estas linhas.
 *
 * Sobre o tamanho dos controles: `md` tem 40px de altura e `lg` tem
 * 44px. A versão anterior usava `py-2` com texto de 13px, o que dava
 * ~32px — abaixo do mínimo de 44px que as diretrizes de toque pedem, e
 * perceptivelmente apertado até no mouse.
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

import { IconChevronDown } from "./icons";

export * from "./icons";

export function cx(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(" ");
}

// ---------------------------------------------------------------
// Botão
// ---------------------------------------------------------------
type Variant = "primary" | "secondary" | "ghost" | "danger" | "quiet";

const VARIANTS: Record<Variant, string> = {
  /* `shadow-brand` dá ao primário um halo da própria cor: é o que faz
   * um botão parecer o destino óbvio da tela, em vez de um retângulo
   * colorido. */
  primary:
    "bg-brand text-brand-ink shadow-brand hover:brightness-[1.08] active:brightness-95",
  secondary:
    "border border-line-strong bg-surface-2 text-ink shadow-ring hover:border-muted/50 hover:bg-surface-3",
  ghost: "text-ink-2 hover:bg-surface-2 hover:text-ink",
  danger: "bg-danger text-white hover:brightness-110 active:brightness-95",
  quiet: "text-muted hover:bg-surface-2 hover:text-ink",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: "sm" | "md" | "lg" | "icon";
  loading?: boolean;
  full?: boolean;
}

const SIZES: Record<NonNullable<ButtonProps["size"]>, string> = {
  sm: "h-8 gap-1.5 rounded-lg px-3 text-xs",
  md: "h-10 gap-2 rounded-lg px-4 text-sm",
  lg: "h-11 gap-2 rounded-xl px-5 text-base",
  icon: "h-9 w-9 rounded-lg",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", loading, full, children, className, ...rest },
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
      className={cx(
        "inline-flex select-none items-center justify-center font-medium",
        // `active:scale-[0.98]` é o retorno tátil que faltava: sem ele,
        // um clique só muda a cor e a interface parece não ter ouvido.
        "transition-all duration-150 ease-out active:scale-[0.98]",
        "disabled:pointer-events-none disabled:opacity-45",
        SIZES[size],
        VARIANTS[variant],
        full && "w-full",
        className,
      )}
      {...rest}
    >
      {loading ? <Spinner /> : null}
      {children}
    </button>
  );
});

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={cx(
        "inline-block h-3.5 w-3.5 shrink-0 animate-spin rounded-full",
        "border-2 border-current border-t-transparent opacity-70",
        className,
      )}
    />
  );
}

// ---------------------------------------------------------------
// Campos
// ---------------------------------------------------------------
/*
 * O anel de foco do campo é `ring`, não troca de borda.
 *
 * Mudar só a cor da borda de 1px é quase invisível em tema escuro. O
 * anel de 3px numa cor fraca do acento aparece sem gritar, e é o padrão
 * que as interfaces boas usam.
 */
const FIELD_BASE = cx(
  "w-full rounded-lg border border-line-strong bg-surface-2 px-3.5 text-sm text-ink",
  "placeholder:text-faint outline-none transition-all duration-150",
  "focus:border-brand focus:bg-surface focus:ring-[3px] focus:ring-brand/20",
  "disabled:cursor-not-allowed disabled:opacity-50",
);

export function Field({
  label,
  hint,
  error,
  children,
  htmlFor,
  className,
}: {
  label: string;
  hint?: ReactNode;
  error?: string | null;
  children: ReactNode;
  htmlFor?: string;
  className?: string;
}) {
  return (
    <div className={cx("space-y-2", className)}>
      <label htmlFor={htmlFor} className="block text-sm font-medium text-ink-2">
        {label}
      </label>
      {children}
      {/* `role="alert"` para o leitor de tela anunciar o erro quando
          aparecer, em vez de a pessoa descobrir ao tabular de volta. */}
      {error ? (
        <p role="alert" className="flex items-center gap-1.5 text-xs text-danger">
          {error}
        </p>
      ) : hint ? (
        <p className="text-xs leading-relaxed text-muted">{hint}</p>
      ) : null}
    </div>
  );
}

export const Input = forwardRef<
  HTMLInputElement,
  InputHTMLAttributes<HTMLInputElement>
>(function Input({ className, ...rest }, ref) {
  return <input ref={ref} className={cx(FIELD_BASE, "h-10", className)} {...rest} />;
});

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement>
>(function Textarea({ className, ...rest }, ref) {
  return (
    <textarea
      ref={ref}
      className={cx(FIELD_BASE, "resize-none py-2.5 leading-relaxed", className)}
      {...rest}
    />
  );
});

/**
 * `<select>` nativo com seta própria.
 *
 * A seta do sistema operacional não respeita a cor do tema e fica
 * errada em UI escura. `appearance-none` a remove, e o chevron vem do
 * nosso conjunto de ícones — mas o elemento continua sendo um `select`
 * de verdade, então teclado, busca por digitação e o seletor nativo do
 * celular seguem funcionando. Um dropdown feito à mão perderia os três.
 */
export const Select = forwardRef<
  HTMLSelectElement,
  SelectHTMLAttributes<HTMLSelectElement> & { bare?: boolean }
>(function Select({ className, bare, children, ...rest }, ref) {
  return (
    <span
      className={cx(
        "relative inline-flex items-center",
        // `min-w-0` em vez de `shrink-0`: com `shrink-0` o seletor de
        // provedor do chat ("Mock (testes) · mock-chat", 247px) não
        // cedia espaço e vazava 10px da janela a 768px, criando barra
        // de rolagem horizontal. A linha já tem `flex-wrap` — o que
        // faltava era deixar o elemento encolher antes de quebrar.
        bare && "min-w-0 max-w-full",
      )}
    >
      <select
        ref={ref}
        className={cx(
          "cursor-pointer appearance-none pr-8 outline-none transition-colors",
          bare
            ? "min-w-0 max-w-full truncate rounded-lg bg-transparent py-1.5 pl-2.5 text-xs font-medium text-muted hover:bg-surface-3 hover:text-ink"
            : cx(FIELD_BASE, "h-10"),
          className,
        )}
        {...rest}
      >
        {children}
      </select>
      <IconChevronDown
        className={cx(
          "pointer-events-none absolute right-2.5 opacity-60",
          bare ? "h-3.5 w-3.5" : "h-4 w-4",
        )}
      />
    </span>
  );
});

/**
 * Interruptor.
 *
 * É um `<button role="switch">`, não um `<div>` com clique: o papel faz
 * o leitor de tela anunciar "ligado/desligado", e o botão dá Espaço e
 * Enter de graça.
 */
export function Switch({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cx(
        "relative h-6 w-11 shrink-0 rounded-full transition-colors duration-200",
        "disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "bg-brand" : "bg-surface-3 ring-1 ring-inset ring-line-strong",
      )}
    >
      <span
        aria-hidden
        className={cx(
          "absolute top-1 h-4 w-4 rounded-full bg-white shadow-sm",
          "transition-transform duration-200 ease-out",
          checked ? "translate-x-6" : "translate-x-1",
        )}
      />
    </button>
  );
}

// ---------------------------------------------------------------
// Superfícies
// ---------------------------------------------------------------
export function Panel({
  children,
  className,
  as: Tag = "section",
  hover,
}: {
  children: ReactNode;
  className?: string;
  as?: "section" | "div" | "article" | "li";
  hover?: boolean;
}) {
  return <Tag className={cx("card", hover && "card-hover", className)}>{children}</Tag>;
}

/** Cabeçalho de painel: título, legenda opcional e ação à direita. */
export function PanelHeader({
  title,
  subtitle,
  action,
  icon,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
      <div className="flex min-w-0 items-start gap-3">
        {icon ? <span className="mt-0.5 text-muted">{icon}</span> : null}
        <div className="min-w-0">
          <h2 className="truncate text-base font-semibold">{title}</h2>
          {subtitle ? (
            <p className="mt-0.5 text-xs leading-relaxed text-muted">{subtitle}</p>
          ) : null}
        </div>
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

/**
 * Cabeçalho de tela.
 *
 * Existe porque faltava hierarquia: nenhuma tela tinha um `<h1>` com
 * tamanho de título, então todas pareciam a mesma lista densa. Aqui o
 * título é 22px e grudado no topo com `.glass`, para não ocupar espaço
 * ao rolar.
 *
 * `width` existe por um erro que só apareceu na captura de tela: o
 * cabeçalho tinha `max-w-5xl` fixo e os corpos usam `3xl` ou `2xl`,
 * então o título começava à esquerda do conteúdo que ele encabeça. Em
 * monitor largo o desalinhamento é de uns 200px, e faz a tela inteira
 * parecer torta.
 */
export function PageHeader({
  title,
  subtitle,
  action,
  children,
  width = "3xl",
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  children?: ReactNode;
  /** Deve casar com o `max-w-*` do corpo da tela. */
  width?: "2xl" | "3xl" | "5xl";
}) {
  const widths = {
    "2xl": "max-w-2xl",
    "3xl": "max-w-3xl",
    "5xl": "max-w-5xl",
  };
  return (
    <header className="glass sticky top-0 z-20 border-b border-line">
      <div
        className={cx(
          "mx-auto flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-4 md:px-7",
          widths[width],
        )}
      >
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-xl font-semibold">{title}</h1>
          {subtitle ? (
            <p className="mt-0.5 truncate text-sm text-muted">{subtitle}</p>
          ) : null}
        </div>
        {action}
      </div>
      {children}
    </header>
  );
}

export function Badge({
  children,
  tone = "neutral",
  solid,
}: {
  children: ReactNode;
  tone?: "neutral" | "ok" | "warn" | "danger" | "brand" | "proposed" | "fixed";
  solid?: boolean;
}) {
  /*
   * Fundo tingido em vez de só borda colorida.
   *
   * A versão anterior usava `border` + `text` na cor, sobre fundo
   * transparente: o selo desaparecia contra o cartão. Um fundo a 12% da
   * cor dá presença sem virar bloco chapado.
   */
  const tones: Record<string, string> = {
    neutral: "bg-surface-3 text-ink-2 ring-line-strong",
    ok: "bg-ok/12 text-ok ring-ok/25",
    warn: "bg-warn/12 text-warn ring-warn/25",
    danger: "bg-danger/12 text-danger ring-danger/25",
    brand: "bg-brand/12 text-brand ring-brand/25",
    proposed: "bg-proposed/12 text-proposed ring-proposed/25",
    fixed: "bg-fixed/12 text-fixed ring-fixed/25",
  };
  const solids: Record<string, string> = {
    neutral: "bg-surface-3 text-ink",
    ok: "bg-ok text-canvas",
    warn: "bg-warn text-canvas",
    danger: "bg-danger text-white",
    brand: "bg-brand text-brand-ink",
    proposed: "bg-proposed text-white",
    fixed: "bg-fixed text-canvas",
  };
  return (
    <span
      className={cx(
        "inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5",
        "text-2xs font-semibold leading-5",
        solid ? solids[tone] : cx("ring-1 ring-inset", tones[tone]),
      )}
    >
      {children}
    </span>
  );
}

/** Barra de progresso, com o valor exposto ao leitor de tela. */
export function Progress({
  value,
  max = 100,
  tone = "brand",
  label,
  className,
}: {
  value: number;
  max?: number;
  tone?: "brand" | "ok" | "warn";
  label?: string;
  className?: string;
}) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
  const tones = { brand: "bg-brand", ok: "bg-ok", warn: "bg-warn" };
  return (
    <div
      role="progressbar"
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
      className={cx("h-1.5 overflow-hidden rounded-full bg-surface-3", className)}
    >
      <div
        className={cx(
          "h-full rounded-full transition-[width] duration-500 ease-out",
          tones[tone],
        )}
        style={{ width: `${pct}%` }}
      />
    </div>
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
  icon,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-3 px-6 py-14 text-center">
      {icon ? (
        <span className="mb-1 grid h-11 w-11 place-items-center rounded-xl bg-surface-2 text-muted ring-1 ring-inset ring-line">
          {icon}
        </span>
      ) : null}
      <p className="text-base font-medium">{title}</p>
      {children ? (
        <p className="max-w-sm text-sm leading-relaxed text-muted">{children}</p>
      ) : null}
      {action ? <div className="mt-1">{action}</div> : null}
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
      className="flex flex-col items-center gap-3 px-6 py-12 text-center"
    >
      <span className="grid h-11 w-11 place-items-center rounded-xl bg-danger/10 text-danger ring-1 ring-inset ring-danger/20">
        <IconAlertInline />
      </span>
      <p className="text-sm text-ink-2">{message}</p>
      {onRetry ? (
        <Button size="sm" variant="secondary" onClick={onRetry}>
          Tentar de novo
        </Button>
      ) : null}
    </div>
  );
}

function IconAlertInline() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      aria-hidden
      className="h-5 w-5"
    >
      <path d="M12 4.5 2.8 20h18.4L12 4.5ZM12 10v4.5M12 17.3v.2" />
    </svg>
  );
}

/** Linhas de esqueleto, para a tela não piscar de vazia a cheia. */
export function SkeletonList({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2.5" aria-hidden>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="card flex items-center gap-3 p-4">
          <div className="skeleton h-9 w-9 rounded-lg" />
          <div className="flex-1 space-y-2">
            <div className="skeleton h-3 w-2/5" />
            <div className="skeleton h-2.5 w-3/5" />
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------
// Diálogo
// ---------------------------------------------------------------
/**
 * Modal sobre `<dialog>` nativo.
 *
 * O elemento nativo dá de graça o que costuma ser reimplementado
 * errado: Escape fecha, o foco fica preso dentro, e o resto da página
 * fica inerte para leitor de tela. Um `<div>` com `position: fixed` não
 * tem nada disso.
 */
export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = "md",
}: {
  /** `lg` para conteudo que empilha cartoes, como as propostas. */
  size?: "md" | "lg";
  open: boolean;
  onClose: () => void;
  title: string;
  description?: ReactNode;
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
      className={cx(
        size === "lg"
          ? "w-[min(46rem,calc(100vw-2rem))]"
          : "w-[min(34rem,calc(100vw-2rem))]",
        "rounded-2xl border border-line-strong",
        "bg-surface p-0 text-ink shadow-float",
        "backdrop:bg-black/65 backdrop:backdrop-blur-sm",
        "open:animate-scale-in",
      )}
    >
      <div className="flex items-start justify-between gap-4 px-6 pb-4 pt-5">
        <div className="min-w-0">
          <h2 id={titleId} className="text-lg font-semibold">
            {title}
          </h2>
          {description ? (
            <p className="mt-1 text-sm leading-relaxed text-muted">{description}</p>
          ) : null}
        </div>
        <Button
          variant="quiet"
          size="icon"
          onClick={onClose}
          aria-label="Fechar"
          className="-mr-2 -mt-1"
        >
          <IconXInline />
        </Button>
      </div>
      {/* O corpo rola sozinho: sem isto, dois cartoes de proposta
          empilhados passam da altura da tela e o rodape com os botoes
          sai de vista, deixando a acao principal inalcancavel. */}
      <div className="max-h-[70vh] overflow-y-auto px-6 pb-5 text-sm">{children}</div>
      {footer ? (
        <div className="flex flex-wrap justify-end gap-2 border-t border-line bg-surface-2/50 px-6 py-4">
          {footer}
        </div>
      ) : null}
    </dialog>
  );
}

function IconXInline() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      aria-hidden
      className="h-4 w-4"
    >
      <path d="M6 6l12 12M18 6 6 18" />
    </svg>
  );
}
