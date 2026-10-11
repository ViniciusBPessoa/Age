import type { Config } from "tailwindcss";

/*
 * Os tokens entram no tema, não só no CSS.
 *
 * Antes, cada componente escrevia `text-[var(--muted)]`. Funciona, mas
 * é ilegível e não tem autocompletar — e o que não é fácil de escrever
 * certo acaba escrito errado: foi assim que surgiram tons fora da
 * paleta espalhados pelas telas.
 *
 * Com `colors` no tema, `text-muted` e `bg-surface` são classes de
 * verdade, e um `bg-[#1a1a1a]` passa a parecer o que é: uma exceção.
 */
export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "var(--canvas)",
        surface: "var(--surface)",
        "surface-2": "var(--surface-2)",
        "surface-3": "var(--surface-3)",
        line: "var(--line)",
        "line-strong": "var(--line-strong)",
        ink: "var(--ink)",
        "ink-2": "var(--ink-2)",
        muted: "var(--muted)",
        faint: "var(--faint)",
        brand: "var(--brand)",
        "brand-soft": "var(--brand-soft)",
        "brand-ink": "var(--brand-ink)",
        ok: "var(--ok)",
        warn: "var(--warn)",
        danger: "var(--danger)",
        fixed: "var(--fixed)",
        flexible: "var(--flexible)",
        proposed: "var(--proposed)",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      /*
       * Escala tipográfica com line-height e tracking embutidos.
       *
       * O erro da versão anterior não foi usar tamanhos errados — foi
       * não ter escala: tudo vivia entre 11px e 14px, então título,
       * legenda e rótulo tinham o mesmo peso visual e nada guiava o
       * olho. Texto grande também precisa de tracking negativo, senão
       * parece esticado.
       */
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem", letterSpacing: "0.02em" }],
        xs: ["0.75rem", { lineHeight: "1.125rem" }],
        sm: ["0.8125rem", { lineHeight: "1.25rem" }],
        base: ["0.9375rem", { lineHeight: "1.5rem" }],
        md: ["1rem", { lineHeight: "1.625rem" }],
        lg: ["1.125rem", { lineHeight: "1.6rem", letterSpacing: "-0.01em" }],
        xl: ["1.375rem", { lineHeight: "1.75rem", letterSpacing: "-0.015em" }],
        "2xl": ["1.75rem", { lineHeight: "2.125rem", letterSpacing: "-0.02em" }],
        "3xl": ["2.125rem", { lineHeight: "2.5rem", letterSpacing: "-0.025em" }],
        "4xl": ["2.75rem", { lineHeight: "3rem", letterSpacing: "-0.03em" }],
      },
      borderRadius: {
        md: "0.5rem",
        lg: "0.75rem",
        xl: "1rem",
        "2xl": "1.25rem",
        "3xl": "1.5rem",
      },
      boxShadow: {
        /* Em fundo quase preto, sombra sozinha não cria profundidade:
         * o que separa as camadas é o anel claro de 1px por cima. */
        ring: "inset 0 1px 0 0 rgb(255 255 255 / 0.04)",
        raised:
          "0 1px 2px 0 rgb(0 0 0 / 0.4), 0 8px 24px -8px rgb(0 0 0 / 0.5), inset 0 1px 0 0 rgb(255 255 255 / 0.05)",
        float:
          "0 2px 4px 0 rgb(0 0 0 / 0.4), 0 20px 50px -12px rgb(0 0 0 / 0.65), inset 0 1px 0 0 rgb(255 255 255 / 0.06)",
        brand: "0 4px 16px -4px rgb(224 134 84 / 0.4)",
      },
      keyframes: {
        "fade-up": {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "scale-in": {
          from: { opacity: "0", transform: "scale(0.97)" },
          to: { opacity: "1", transform: "scale(1)" },
        },
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.28s cubic-bezier(0.22, 1, 0.36, 1) both",
        "fade-in": "fade-in 0.2s ease-out both",
        "scale-in": "scale-in 0.18s cubic-bezier(0.22, 1, 0.36, 1) both",
      },
      transitionTimingFunction: {
        /* Saída rápida, chegada suave: é o que faz o toque parecer
         * resposta em vez de atraso. */
        out: "cubic-bezier(0.22, 1, 0.36, 1)",
      },
    },
  },
  plugins: [],
} satisfies Config;
