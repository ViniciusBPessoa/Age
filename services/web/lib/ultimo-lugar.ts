/**
 * Onde a pessoa estava em cada área.
 *
 * Pedido direto: "clico em agenda, ele salva a última visualização que
 * eu tive; quando eu voltar para chat, ele está com o último chat que
 * eu estava aberto. Para eu não ter essa dificuldade maior de resetar
 * o que eu estava fazendo."
 *
 * A navegação tinha `href: "/chat"` fixo, então voltar ao chat abria
 * sempre a tela vazia, e a agenda remontava com `useState` zerado:
 * quem estava vendo o mês caía na semana de hoje a cada ida e volta.
 *
 * `localStorage` e não o servidor: é preferência de navegador, não
 * dado da conta. Guardar no banco faria a agenda do celular pular para
 * onde o desktop estava, o que ninguém pediu.
 *
 * Toda leitura e escrita está embrulhada: em navegação privada o
 * acessor pode lançar, e perder a memória do último lugar não é motivo
 * para a tela não abrir.
 */

const CHAVE_CONVERSA = "age-ultima-conversa";
const CHAVE_VISTA = "age-ultima-vista-agenda";
const CHAVE_AGRUPAMENTO = "age-agrupamento-tarefas";

/** As visualizações da agenda. Espelha `TimelineView`. */
export type VistaAgenda = "day" | "week" | "month" | "agenda";

/** Como a tela de tarefas agrupa. Espelha `GroupBy`. */
export type AgrupamentoTarefas = "due" | "horario" | "tema" | "list" | "priority";

const AGRUPAMENTOS: AgrupamentoTarefas[] = [
  "due",
  "horario",
  "tema",
  "list",
  "priority",
];

function ler(chave: string): string | null {
  try {
    return localStorage.getItem(chave);
  } catch {
    return null;
  }
}

function gravar(chave: string, valor: string): void {
  try {
    localStorage.setItem(chave, valor);
  } catch {
    // Navegação privada, ou cota cheia. A sessão atual continua
    // funcionando; só não é lembrada na próxima.
  }
}

function apagar(chave: string): void {
  try {
    localStorage.removeItem(chave);
  } catch {
    // Mesmo caso do `gravar`.
  }
}

/**
 * Valida o que veio do armazenamento.
 *
 * O conteúdo de `localStorage` é entrada não confiável: sobrevive a
 * deploys, pode ter sido escrito por uma versão antiga e pode ser
 * editado à mão no devtools. Um id inválido viraria `/chat/lixo` e uma
 * tela de erro no lugar da conversa.
 */
function pareceUuid(valor: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(valor);
}

export function lerUltimaConversa(): string | null {
  const valor = ler(CHAVE_CONVERSA);
  if (valor === null) return null;
  return pareceUuid(valor) ? valor : null;
}

export function guardarUltimaConversa(id: string): void {
  gravar(CHAVE_CONVERSA, id);
}

/**
 * Esquece a conversa.
 *
 * Chamado ao apagar: sem isto, a navegação levaria a uma conversa que
 * não existe mais, e o erro apareceria no lugar da tela inicial.
 */
export function esquecerUltimaConversa(): void {
  apagar(CHAVE_CONVERSA);
}

export function lerUltimaVista(): VistaAgenda | null {
  const valor = ler(CHAVE_VISTA);
  return valor === "day" || valor === "week" || valor === "month" || valor === "agenda"
    ? valor
    : null;
}

/**
 * Guarda só a visualização, nunca a data.
 *
 * Decisão do usuário quando perguntei: lembrar dia/semana/mês, mas
 * sempre abrir em hoje. Guardar a data faria quem volta dias depois
 * cair numa semana velha sem perceber — e "o que eu tenho hoje" é a
 * pergunta que a agenda responde na maior parte das vezes.
 */
export function guardarUltimaVista(vista: VistaAgenda): void {
  gravar(CHAVE_VISTA, vista);
}

/**
 * Como a tela de tarefas estava agrupada da última vez.
 *
 * Valida contra a lista fechada pelo mesmo motivo de `lerUltimaVista`:
 * `localStorage` é entrada NÃO confiável — sobrevive a deploy e qualquer
 * um edita pelo devtools. Um valor fora da lista viraria um `groupBy`
 * que não casa com nenhum ramo, e a tela renderizaria vazia.
 */
export function lerAgrupamentoTarefas(): AgrupamentoTarefas | null {
  const valor = ler(CHAVE_AGRUPAMENTO);
  return AGRUPAMENTOS.includes(valor as AgrupamentoTarefas)
    ? (valor as AgrupamentoTarefas)
    : null;
}

/**
 * Guarda só o MODO, nunca quais grupos estavam fechados.
 *
 * Os ids de grupo são ids de evento e nomes de tema, que mudam a cada
 * sessão aplicada ou concluída. Guardar o conjunto acumularia id morto
 * para sempre, e um dia esconderia um grupo novo por coincidência de
 * id. O modo é preferência durável; o que está dobrado é efêmero.
 */
export function guardarAgrupamentoTarefas(valor: AgrupamentoTarefas): void {
  gravar(CHAVE_AGRUPAMENTO, valor);
}
