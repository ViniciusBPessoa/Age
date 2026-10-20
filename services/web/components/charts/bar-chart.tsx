/**
 * Barras verticais em SVG, sem dependência.
 *
 * Genérico de propósito: recebe `{label, value, title}[]` e não sabe o
 * que é token nem custo. É o mesmo encapsulamento que
 * `architecture.test.ts` cobra do `react-big-calendar` — se um dia uma
 * biblioteca de gráfico entrar no projeto, ela entra aqui e em mais
 * lugar nenhum.
 *
 * ## Por que `viewBox` e não medir o contêiner
 *
 * Sem `useEffect`, sem `ResizeObserver`, sem ler layout. Em jsdom
 * `getBoundingClientRect()` devolve zero em tudo, e um gráfico que
 * dependa de medida renderiza vazio no teste — seria estruturalmente
 * não testável. Com `viewBox` em unidades próprias e largura 100% no
 * CSS, o SVG escala sozinho e a árvore é determinística.
 *
 * ## Acessibilidade
 *
 * `role="img"` com `aria-label` resumindo, mais uma tabela `sr-only`
 * com os números. Um `<svg>` sem isso é invisível para leitor de tela,
 * e a tabela tem um efeito colateral bom: dá ao teste uma asserção
 * sobre conteúdo, não sobre geometria.
 */

const L = 4; // margem lateral, em unidades do viewBox
const A = 100; // altura útil
const ESPACO = 0.22; // fração da faixa que vira respiro entre barras

export interface Barra {
  /** Chave única e rótulo curto do eixo. */
  label: string;
  value: number;
  /** Texto do tooltip nativo; cai no label quando ausente. */
  title?: string;
  /** Rótulo sob a barra. Omitir esconde (série longa fica ilegível). */
  tick?: string;
}

export function BarChart({
  bars,
  ariaLabel,
  caption,
  formatValue = (v) => String(v),
  className = "",
}: {
  bars: Barra[];
  ariaLabel: string;
  caption: string;
  formatValue?: (v: number) => string;
  className?: string;
}) {
  const maximo = Math.max(0, ...bars.map((b) => b.value));
  const largura = Math.max(bars.length, 1);
  const faixa = (largura * 10) / largura; // 10 unidades por barra
  const total = largura * 10;

  return (
    <div className={className}>
      {/* `preserveAspectRatio="none"` estica o SVG, e isso vale para as
          barras mas DEFORMA o texto dos rótulos. Eles saem num `<svg>`
          irmão, com proporção preservada, sobreposto pelo mesmo
          `viewBox` — assim a barra acompanha a largura e a letra
          continua redonda. */}
      <svg
        viewBox={`0 0 ${total} ${A}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={ariaLabel}
        className="h-36 w-full"
      >
        {/* Grade: três linhas de referência, puramente visual. */}
        <g aria-hidden="true">
          {[0.25, 0.5, 0.75].map((f) => (
            <line
              key={f}
              x1={0}
              x2={total}
              y1={A * f}
              y2={A * f}
              className="stroke-line"
              strokeWidth={0.5}
              vectorEffect="non-scaling-stroke"
            />
          ))}
          <line
            x1={0}
            x2={total}
            y1={A}
            y2={A}
            className="stroke-line-strong"
            strokeWidth={0.5}
            vectorEffect="non-scaling-stroke"
          />
        </g>

        {bars.map((b, i) => {
          const bruta = maximo > 0 ? (b.value / maximo) * A : 0;
          // Altura mínima de 1 para valor positivo: um dia com 50
          // tokens numa escala de 500 mil arredonda para zero e a
          // barra SOME. "Não usei nada" e "usei pouco" viram a mesma
          // imagem, que é a pior confusão possível num gráfico de uso.
          const altura = b.value > 0 ? Math.max(1, bruta) : 0;
          const x = i * faixa + faixa * (ESPACO / 2);
          const w = faixa * (1 - ESPACO);

          return (
            <rect
              key={b.label}
              data-barra={b.label}
              data-valor={b.value}
              x={x}
              // Dia sem uso ganha um traço de 0,6 na linha de base, em
              // cinza. Altura zero some, e um gráfico onde o dia vazio
              // é invisível não distingue "não usei" de "o período
              // acabou aqui" — a base marcada diz "este dia existe e
              // não teve uso", sem afirmar uso nenhum.
              y={b.value > 0 ? A - altura : A - 0.6}
              width={w}
              height={b.value > 0 ? altura : 0.6}
              rx={0.8}
              className={b.value > 0 ? "fill-brand" : "fill-line-strong"}
            >
              <title>{b.title ?? `${b.label}: ${formatValue(b.value)}`}</title>
            </rect>
          );
        })}
      </svg>

      {/* Rótulos em HTML, não em `<text>` dentro do SVG.
          Dois motivos: `preserveAspectRatio="none"` deformaria a letra
          junto com as barras, e um `<text>` centrado na última faixa
          vaza pela borda do viewBox e aparece cortado — foi o que a
          captura mostrou ("20/1" em vez de "20/10"). Em HTML o
          alinhamento das pontas é `flex`, e o texto nunca sai da
          caixa. */}
      <div
        aria-hidden="true"
        className="mt-1 flex justify-between px-0.5 text-2xs text-faint"
      >
        {bars
          .filter((b) => b.tick)
          .map((b) => (
            <span key={`t-${b.label}`} className="tnum">
              {b.tick}
            </span>
          ))}
      </div>

      {/* O mesmo dado em texto. É o que o leitor de tela lê, e o que o
          teste consulta sem tocar em geometria. */}
      <table className="sr-only">
        <caption>{caption}</caption>
        <tbody>
          {bars.map((b) => (
            <tr key={b.label}>
              <th scope="row">{b.label}</th>
              <td>{formatValue(b.value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
