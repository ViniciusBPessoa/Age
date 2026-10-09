/**
 * Os dados de apresentação de cada provedor de LLM.
 *
 * §8.1 pede três coisas que o catálogo da API não tem, e cada uma
 * existe contra um ponto de desistência concreto do onboarding:
 *
 *   - **link direto para criar a chave** — sem isso a pessoa sai do app
 *     para procurar onde se cadastra, e não volta;
 *   - **custo estimado em reais, por conversa** — "chave de API" soa
 *     caro e corporativo, e "~R$ 0,05" desfaz isso;
 *   - **a nota de quem tem cota grátis**, que é o caminho de menor
 *     atrito para quem não quer pôr cartão.
 *
 * Isto não vive no backend porque é texto de marketing de terceiro, que
 * muda quando o provedor muda a tabela de preços — e porque não
 * participa de nenhuma decisão de domínio.
 *
 * ## A lista aqui NÃO define quais provedores existem
 *
 * Quem define é o registry do worker, via `GET /llm/providers` (§3.1):
 * "adicionar um provedor novo = criar um arquivo neste diretório com
 * `@register_llm`. Nada mais muda." Se esta tabela fosse a fonte, essa
 * promessa quebraria no front — alguém registraria um adaptador e ele
 * não apareceria na tela.
 *
 * Então a tela itera o que a API devolve, e busca aqui o enfeite. Um
 * provedor sem entrada nesta tabela aparece com o nome que a API deu e
 * sem link — degradado, mas presente. Mesmo princípio de §3.3.2:
 * degradar é aceitável, desaparecer não.
 *
 * Hoje o registry tem `deepseek`, `openai` e `mock`. Gemini e Ollama
 * têm entrada aqui porque §8.1 os descreve e o M1 foi escopado como
 * "adaptador Gemini **ou** OpenAI" — quando o adaptador existir, a
 * tela já sabe apresentá-lo.
 */

export interface ProviderPresentation {
  /** Onde a pessoa cria a chave. `null` quando não precisa de chave. */
  keyUrl: string | null;
  /** Custo médio estimado de uma conversa, em BRL. */
  costPerConversation: number | null;
  /** Uma linha, curta, que ajuda a escolher. */
  note: string;
  /** Destaque: hoje, ter cota grátis. */
  highlight?: "free_tier" | "cheapest" | "local";
}

export const PRESENTATION: Record<string, ProviderPresentation> = {
  deepseek: {
    keyUrl: "https://platform.deepseek.com/api_keys",
    costPerConversation: 0.05,
    note: "O mais barato dos que pedem cartão.",
    highlight: "cheapest",
  },
  openai: {
    keyUrl: "https://platform.openai.com/api-keys",
    costPerConversation: 0.3,
    note: "O mais conhecido; qualidade alta e preço maior.",
  },
  gemini: {
    keyUrl: "https://aistudio.google.com/apikey",
    costPerConversation: 0,
    note: "Tem cota grátis — dá para usar sem cadastrar cartão.",
    highlight: "free_tier",
  },
  ollama: {
    keyUrl: null,
    costPerConversation: 0,
    note: "Roda no seu PC. Nada sai daqui, e não custa nada.",
    highlight: "local",
  },
  anthropic: {
    keyUrl: "https://console.anthropic.com/settings/keys",
    costPerConversation: 0.35,
    note: "Bom em seguir instrução longa.",
  },
  mock: {
    keyUrl: null,
    costPerConversation: 0,
    note: "Respostas fixas, para teste. Não usa modelo nenhum.",
  },
};

export const HIGHLIGHT_LABEL: Record<
  NonNullable<ProviderPresentation["highlight"]>,
  string
> = {
  free_tier: "tem cota grátis",
  cheapest: "mais barato",
  local: "roda no seu PC",
};

/**
 * `mock` é útil em teste e confuso no onboarding: alguém escolheria
 * "Mock (testes)" sem saber que o assistente deixaria de pensar.
 * Continua acessível em Configurações, onde o contexto é outro.
 */
export const HIDDEN_IN_ONBOARDING = new Set(["mock"]);

export function presentationFor(key: string): ProviderPresentation {
  return (
    PRESENTATION[key] ?? {
      keyUrl: null,
      costPerConversation: null,
      note: "",
    }
  );
}
