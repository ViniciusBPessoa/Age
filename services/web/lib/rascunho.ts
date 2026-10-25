/**
 * O texto não enviado de cada conversa (§8.1).
 *
 * O relato: *"quando eu saí do chat para ver meu calendário, eu voltei
 * e ele apagou a mensagem que estava escrita lá"*. O campo era
 * `useState("")` puro, então sair da tela desmontava o componente e o
 * texto morria junto.
 *
 * ## Por que `localStorage` e não `sessionStorage`
 *
 * O pedido foi explícito: *"quando eu quiser sair e voltar, ele vai
 * estar salvo para qualquer chat"*. `sessionStorage` morre ao fechar a
 * aba, o que atende "ir ao calendário e voltar" mas não "fechar o
 * navegador e continuar amanhã" — e um rascunho perdido no segundo
 * caso é a mesma frustração do primeiro.
 *
 * ## Por que tudo em try/catch
 *
 * Em aba anônima, com cookies de terceiros bloqueados ou com a cota
 * estourada, o acesso **lança** em vez de devolver `null`. Um chat que
 * não abre por causa de rascunho é muito pior que um rascunho perdido,
 * então toda leitura e escrita degradam para "sem rascunho".
 *
 * ## Uma chave por conversa
 *
 * Chave única faria o texto de um chat aparecer no outro. A conversa
 * ainda não criada usa `nova`, e o rascunho migra quando ela ganha id.
 */

const PREFIXO = "age:rascunho:";

/** Teto por rascunho. Protege contra colar um livro no campo. */
const MAXIMO = 8000;

/** Evento disparado quando um rascunho muda, para a lista reagir. */
export const EVENTO_RASCUNHO = "age:rascunho-mudou";

function chave(conversaId: string | null): string {
  return `${PREFIXO}${conversaId ?? "nova"}`;
}

export function lerRascunho(conversaId: string | null): string {
  try {
    return window.localStorage.getItem(chave(conversaId)) ?? "";
  } catch {
    return "";
  }
}

export function salvarRascunho(conversaId: string | null, texto: string): void {
  try {
    const k = chave(conversaId);
    if (texto.trim().length === 0) {
      window.localStorage.removeItem(k);
    } else {
      window.localStorage.setItem(k, texto.slice(0, MAXIMO));
    }
    // A lista de conversas não tem como saber que isto mudou: o
    // evento `storage` do navegador só dispara em OUTRAS abas.
    window.dispatchEvent(new Event(EVENTO_RASCUNHO));
  } catch {
    // Sem rascunho é degradação aceitável; quebrar o chat não é.
  }
}

export function limparRascunho(conversaId: string | null): void {
  salvarRascunho(conversaId, "");
}

/**
 * Move o rascunho de "nova" para a conversa recém-criada.
 *
 * A primeira mensagem é escrita antes de a conversa existir. Sem isto,
 * o texto ficaria preso na chave `nova` e a marca de pendente
 * apareceria na conversa errada.
 */
export function migrarRascunho(novoId: string): void {
  const texto = lerRascunho(null);
  if (!texto) return;
  limparRascunho(null);
  salvarRascunho(novoId, texto);
}

/** Os ids de conversa que têm texto não enviado. */
export function conversasComRascunho(): Set<string> {
  const out = new Set<string>();
  try {
    for (let i = 0; i < window.localStorage.length; i += 1) {
      const k = window.localStorage.key(i);
      if (!k?.startsWith(PREFIXO)) continue;
      const valor = window.localStorage.getItem(k);
      if (valor && valor.trim().length > 0) {
        out.add(k.slice(PREFIXO.length));
      }
    }
  } catch {
    // Sem acesso ao armazenamento: nenhuma marca, e a lista funciona.
  }
  return out;
}
