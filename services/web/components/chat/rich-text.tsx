/**
 * Renderização do texto do modelo (§8.1).
 *
 * O Age mostrava a resposta como texto cru dentro de um `<p>` com
 * `whitespace-pre-wrap`. O modelo escreve em Markdown — é o que todo
 * LLM faz por padrão, e o DeepSeek faz bastante — então a tela exibia
 * literalmente ```` ``` ````, `**` e `|---|` no meio da conversa. Uma
 * resposta com plano de estudo em tabela ficava ilegível.
 *
 * ## Por que sem biblioteca
 *
 * `react-markdown` + `remark-gfm` resolveriam mais casos, mas:
 *
 * - O build da imagem `web` já falha por conflito de peer deps
 *   (`vite@7` quer `@types/node >=22.12`, o projeto fixa `22.10.1`).
 *   Somar dependência agora é somar risco a um build que não fecha.
 * - Markdown de LLM num chat é um subconjunto pequeno e previsível:
 *   bloco de código, negrito, itálico, código inline, lista, título,
 *   tabela e link. Não há HTML embutido para sanitizar.
 *
 * O link entrou com a busca na web (§5.1.3): sem ele a resposta exibia
 * `[título](url)` cru, e a lista de fontes — que é o ponto inteiro de
 * pesquisar — não era clicável. Agora os links VÊM de terceiros, então
 * o esquema é validado (só `http`/`https`) e a âncora leva
 * `rel="noopener noreferrer"`.
 *
 * ## Segurança
 *
 * Nada aqui usa `dangerouslySetInnerHTML`. Tudo vira elemento React
 * com o texto como filho, então o conteúdo é escapado pelo próprio
 * React. Um modelo que devolvesse `<script>` o veria exibido como
 * texto, que é o comportamento correto.
 */

import { Fragment, type ReactNode } from "react";

/** Partes de uma linha: negrito, itálico e código inline. */
function inline(texto: string, chave: string): ReactNode[] {
  const saida: ReactNode[] = [];
  // Uma regex só, com alternativas, para que a ordem de precedência
  // seja a do texto e não a da ordem em que eu aplicaria substituições.
  // `**` antes de `*` importa: com `*` primeiro, "**x**" viraria
  // itálico vazio seguido de lixo.
  const padrao =
    /(\[[^\]\n]+\]\([^)\s]+\))|(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*)|(__[^_\n]+__)/g;
  let ultimo = 0;
  let m: RegExpExecArray | null;
  let i = 0;

  while ((m = padrao.exec(texto)) !== null) {
    if (m.index > ultimo) saida.push(texto.slice(ultimo, m.index));
    const t = m[0];
    i += 1;
    const k = `${chave}-i${i}`;

    if (t.startsWith("[")) {
      const corte = t.lastIndexOf("](");
      const rotulo = t.slice(1, corte);
      const href = t.slice(corte + 2, -1);
      /*
       * So `http(s)`. Este texto vem do MODELO, que por sua vez
       * resumiu conteudo de sites de terceiros: confiar no esquema
       * faria `javascript:` virar ancora executavel. Esquema
       * desconhecido fica como texto -- visivel e inofensivo.
       */
      const seguro = /^https?:\/\//i.test(href);
      saida.push(
        seguro ? (
          <a
            key={k}
            href={href}
            target="_blank"
            /*
             * `noopener` impede a pagina aberta de mexer nesta por
             * `window.opener`; `noreferrer` evita vazar a URL da
             * conversa no cabecalho Referer do site de destino.
             */
            rel="noopener noreferrer"
            className="text-brand underline underline-offset-2 hover:brightness-110"
          >
            {rotulo}
          </a>
        ) : (
          <Fragment key={k}>{t}</Fragment>
        ),
      );
    } else if (t.startsWith("`")) {
      saida.push(
        <code
          key={k}
          className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[0.9em] text-ink"
        >
          {t.slice(1, -1)}
        </code>,
      );
    } else if (t.startsWith("**") || t.startsWith("__")) {
      saida.push(
        <strong key={k} className="font-semibold text-ink">
          {t.slice(2, -2)}
        </strong>,
      );
    } else {
      saida.push(<em key={k}>{t.slice(1, -1)}</em>);
    }
    ultimo = m.index + t.length;
  }

  if (ultimo < texto.length) saida.push(texto.slice(ultimo));
  return saida;
}

interface Bloco {
  tipo: "codigo" | "lista" | "titulo" | "paragrafo" | "tabela";
  linhas: string[];
  lingua?: string;
  nivel?: number;
}

/** Quebra o texto em blocos antes de renderizar.
 *
 * Em dois passos (blocos, depois inline) porque um bloco de código não
 * pode ter o conteúdo interpretado: `**` dentro de código é `**`.
 */
function blocos(texto: string): Bloco[] {
  const linhas = texto.split("\n");
  const out: Bloco[] = [];
  let i = 0;

  while (i < linhas.length) {
    const linha = linhas[i];

    // Bloco de código cercado. Fecha no ``` seguinte ou no fim do
    // texto — o "ou no fim" não é detalhe: durante o streaming a
    // cerca de fechamento ainda não chegou, e sem isso o bloco
    // inteiro desapareceria da tela enquanto o modelo escreve.
    const cerca = linha.match(/^\s*```(\w*)\s*$/);
    if (cerca) {
      const corpo: string[] = [];
      i += 1;
      while (i < linhas.length && !/^\s*```\s*$/.test(linhas[i])) {
        corpo.push(linhas[i]);
        i += 1;
      }
      i += 1; // consome a cerca final, se houver
      out.push({ tipo: "codigo", linhas: corpo, lingua: cerca[1] || undefined });
      continue;
    }

    const titulo = linha.match(/^(#{1,4})\s+(.*)$/);
    if (titulo) {
      out.push({
        tipo: "titulo",
        linhas: [titulo[2]],
        nivel: titulo[1].length,
      });
      i += 1;
      continue;
    }

    // Tabela: linha com `|` seguida da linha de separação `|---|`.
    if (
      linha.includes("|") &&
      i + 1 < linhas.length &&
      /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(linhas[i + 1])
    ) {
      const corpo: string[] = [linha];
      i += 2; // pula o separador
      while (i < linhas.length && linhas[i].includes("|")) {
        corpo.push(linhas[i]);
        i += 1;
      }
      out.push({ tipo: "tabela", linhas: corpo });
      continue;
    }

    if (/^\s*([-*+]|\d+\.)\s+/.test(linha)) {
      const corpo: string[] = [];
      while (i < linhas.length && /^\s*([-*+]|\d+\.)\s+/.test(linhas[i])) {
        corpo.push(linhas[i].replace(/^\s*([-*+]|\d+\.)\s+/, ""));
        i += 1;
      }
      out.push({ tipo: "lista", linhas: corpo });
      continue;
    }

    if (linha.trim() === "") {
      i += 1;
      continue;
    }

    // Parágrafo: junta até a linha vazia ou o começo de outro bloco.
    const corpo: string[] = [];
    while (
      i < linhas.length &&
      linhas[i].trim() !== "" &&
      !/^\s*```/.test(linhas[i]) &&
      !/^#{1,4}\s/.test(linhas[i]) &&
      !/^\s*([-*+]|\d+\.)\s+/.test(linhas[i])
    ) {
      corpo.push(linhas[i]);
      i += 1;
    }
    out.push({ tipo: "paragrafo", linhas: corpo });
  }

  return out;
}

function celulas(linha: string): string[] {
  return linha
    .replace(/^\s*\|/, "")
    .replace(/\|\s*$/, "")
    .split("|")
    .map((c) => c.trim());
}

/**
 * Texto do assistente, com o Markdown que o modelo escreve.
 *
 * `prose` não é usado de propósito: o projeto não tem o plugin de
 * tipografia do Tailwind, e as classes aqui seguem as mesmas variáveis
 * de cor das outras telas (§8.2).
 */
export function RichText({ text }: { text: string }) {
  return (
    <div className="space-y-3 text-base leading-[1.7] text-ink-2">
      {blocos(text).map((b, idx) => {
        const k = `b${idx}`;

        if (b.tipo === "codigo") {
          return (
            <pre
              key={k}
              className="overflow-x-auto rounded-xl border border-line bg-surface-2 px-3.5 py-3 text-[0.86rem] leading-relaxed"
            >
              <code className="font-mono text-ink">{b.linhas.join("\n")}</code>
            </pre>
          );
        }

        if (b.tipo === "titulo") {
          const tamanho =
            b.nivel === 1
              ? "text-lg font-semibold"
              : b.nivel === 2
                ? "text-base font-semibold"
                : "text-sm font-semibold";
          return (
            <p key={k} className={`${tamanho} text-ink`}>
              {inline(b.linhas[0], k)}
            </p>
          );
        }

        if (b.tipo === "lista") {
          return (
            <ul key={k} className="space-y-1.5 pl-1">
              {b.linhas.map((item, j) => (
                <li key={`${k}-${j}`} className="flex gap-2.5">
                  <span aria-hidden className="pt-[0.45rem] text-faint">
                    <svg width="5" height="5" viewBox="0 0 5 5">
                      <circle cx="2.5" cy="2.5" r="2.5" fill="currentColor" />
                    </svg>
                  </span>
                  <span className="min-w-0 flex-1">{inline(item, `${k}-${j}`)}</span>
                </li>
              ))}
            </ul>
          );
        }

        if (b.tipo === "tabela") {
          const [cabecalho, ...corpo] = b.linhas;
          return (
            // `overflow-x-auto` no entorno: uma tabela de plano de
            // estudo passa da largura no celular, e sem isto ela
            // empurraria a página inteira (o mesmo defeito que o chat
            // já teve com rolagem horizontal).
            <div key={k} className="overflow-x-auto rounded-xl border border-line">
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr className="bg-surface-2">
                    {celulas(cabecalho).map((c, j) => (
                      <th
                        key={`${k}-h${j}`}
                        className="px-3 py-2 text-left font-semibold text-ink"
                      >
                        {inline(c, `${k}-h${j}`)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {corpo.map((linha, j) => (
                    <tr key={`${k}-r${j}`} className="border-t border-line">
                      {celulas(linha).map((c, x) => (
                        <td key={`${k}-r${j}c${x}`} className="px-3 py-2">
                          {inline(c, `${k}-r${j}c${x}`)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }

        return (
          <p key={k}>
            {b.linhas.map((linha, j) => (
              <Fragment key={`${k}-l${j}`}>
                {j > 0 ? " " : null}
                {inline(linha, `${k}-l${j}`)}
              </Fragment>
            ))}
          </p>
        );
      })}
    </div>
  );
}
