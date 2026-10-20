/**
 * Uma barra horizontal segmentada, com legenda.
 *
 * Responde "quanto cada um pesa no total?" — a pergunta de uma quebra
 * por modelo. Pizza responderia o mesmo pior: comparar ângulo é mais
 * difícil que comparar comprimento, e com dois ou três segmentos a
 * pizza vira um símbolo sem informação.
 *
 * `viewBox="0 0 100 8"` faz a largura ser literalmente a porcentagem:
 * o cálculo é `fração * 100`, sem escala intermediária, e o teste pode
 * verificar que os segmentos somam 100.
 */

// Tokens do tema, nesta ordem. Nada de cor literal: `tokens-de-cor`
// denuncia `fill-[#...]` avulso, e cor fora do tema quebra no claro ou
// no escuro — sempre no que a pessoa não está usando na hora.
const PALETA = [
  "fill-brand",
  "fill-proposed",
  "fill-flexible",
  "fill-ok",
  "fill-warn",
] as const;

export const CORES_DE_LEGENDA = [
  "bg-brand",
  "bg-proposed",
  "bg-flexible",
  "bg-ok",
  "bg-warn",
] as const;

export interface Fatia {
  label: string;
  value: number;
}

export function StackedBar({
  slices,
  ariaLabel,
  className = "",
}: {
  slices: Fatia[];
  ariaLabel: string;
  className?: string;
}) {
  const total = slices.reduce((s, f) => s + f.value, 0);
  if (total <= 0) return null;

  let offset = 0;
  const segmentos = slices.map((f, i) => {
    const largura = (f.value / total) * 100;
    const seg = { ...f, x: offset, largura, indice: i };
    offset += largura;
    return seg;
  });

  return (
    <svg
      viewBox="0 0 100 8"
      preserveAspectRatio="none"
      role="img"
      aria-label={ariaLabel}
      className={`h-2 w-full ${className}`}
    >
      {segmentos.map((s) => (
        <rect
          key={s.label}
          data-fatia={s.label}
          data-largura={s.largura.toFixed(2)}
          x={s.x}
          y={0}
          width={s.largura}
          height={8}
          className={PALETA[s.indice % PALETA.length]}
        >
          <title>{`${s.label}: ${s.largura.toFixed(1)}%`}</title>
        </rect>
      ))}
    </svg>
  );
}
