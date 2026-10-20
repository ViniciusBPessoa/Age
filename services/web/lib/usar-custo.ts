/**
 * A porta única do dinheiro.
 *
 * Todo lugar que fosse escrever "US$" passa por aqui. É o que torna
 * "esconder o custo dos usuários finais" a mudança de UM valor em vez
 * de uma caçada a JSX espalhado — e há um teste de arquitetura que
 * falha se alguém escrever um valor em dólar fora dos componentes de
 * custo.
 *
 * A resposta vem do servidor (`show_cost` / `show_cost_usd`), não de
 * uma constante local: o operador precisa poder desligar sem build, e
 * por conta se quiser.
 */

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api-client";
import type { Preferences } from "@/lib/types";

export function useMostrarCusto(): boolean {
  const { data } = useQuery({
    queryKey: ["preferences"],
    queryFn: () => api.get<Preferences>("/preferences"),
    staleTime: 5 * 60_000,
  });

  // `false` enquanto carrega, e isto é deliberado: com o default
  // invertido o custo APARECERIA por uma fração de segundo em toda
  // carga de tela antes de sumir — exatamente o que a flag existe para
  // impedir. Aparecer tarde é discreto; piscar é um vazamento visível.
  return data?.show_cost_usd ?? false;
}

/**
 * Custo em dólar, com dígitos suficientes para não virar zero.
 *
 * Uma resposta com prompt cacheado custa na casa de US$ 0,00002. Com
 * duas casas decimais — o default de toda formatação de moeda — isso
 * vira "US$ 0,00", que AFIRMA gratuidade. Um rodapé dizendo "US$ 0,00"
 * em toda mensagem é pior que não mostrar nada.
 *
 * A escala vem da magnitude:
 *
 *     >= 1      US$ 12,34
 *     >= 0,1    US$ 0,98
 *     >= 0,01   US$ 0,0123
 *     >  0      US$ 0,000021
 *     == 0      "grátis" (modelo com preço zero declarado)
 *
 * A faixa de 0,1 existe porque a tela mostrou "US$ 0,9841" num total de
 * período: quatro casas num valor perto de um real fazem o número
 * parecer medição de laboratório, e ninguém lê a quarta casa de um
 * total. Abaixo de dez centavos as casas voltam, porque aí elas são a
 * diferença entre um número e um zero.
 *
 * `null` nunca chega aqui: quem decide o que fazer com "não sei quanto
 * custou" é quem renderiza, porque a frase muda com o contexto.
 */
export function formatUsd(valor: number): string {
  if (valor === 0) return "grátis";

  const casas = valor >= 0.1 ? 2 : valor >= 0.01 ? 4 : 6;
  return `US$ ${valor.toFixed(casas).replace(".", ",")}`;
}

/** Tokens em escala legível: 2848884 vira "2,8M". */
export function formatTokens(valor: number): string {
  if (valor >= 1_000_000) {
    return `${(valor / 1_000_000).toFixed(1).replace(".", ",")}M`;
  }
  if (valor >= 1_000) {
    return `${(valor / 1_000).toFixed(1).replace(".", ",")}k`;
  }
  return String(valor);
}
