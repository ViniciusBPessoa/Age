/**
 * A tela principal (§8.1): conversa à esquerda, sidebar, e o plano
 * aparecendo inline quando é proposto.
 *
 * O ciclo de um turno, do ponto de vista desta tela:
 *
 *   1. POST /conversations/{id}/messages  -> 202 {turn_id, stream_url}
 *   2. abre o SSE em /turns/{id}/stream
 *   3. tool_start/tool_end  -> linhas de progresso (§8.1)
 *   4. token                -> texto crescendo
 *   5. plan                 -> busca o plano e mostra o diff
 *   6. done                 -> recarrega a conversa e encerra
 *
 * O passo 5 merece nota, e já custou um bug que chegou ao usuário.
 * O diff completo vem por HTTP, não pelo stream, porque ele é grande e
 * porque ter endereço é o que permite recarregar a página sem perder a
 * proposta. Mas o `plan_id` do evento **não serve** como endereço:
 *
 * O worker publica `plan_id=turn_id` (ver `run_turn.py`, com o
 * comentário "resolvido na promoção"), porque ele não pode saber o id
 * final — quem converte o rascunho em plano é o core-api, em
 * `promote_drafts`, gerando um id NOVO. A resolução que o comentário
 * prometia nunca existiu aqui, então esta tela pedia
 * `GET /plans/{turn_id}`, levava 404, e o cartão de aprovação
 * simplesmente não aparecia.
 *
 * O efeito para quem usa foi pior que uma tela vazia: o Age respondia
 * em texto que ia agendar as coisas, e nada aparecia para aprovar.
 * Parecia que tinha agendado sozinho, sem pedir licença — exatamente o
 * contrário do que §5.8 garante. (Nada era escrito na agenda; medido:
 * zero eventos até o `/apply`.)
 *
 * A correção busca o plano `proposed` mais recente DESTA conversa, que
 * é o que o `GET /plans?status=proposed` já promove e ordena. Filtrar
 * por conversa importa: um plano antigo não resolvido em outra
 * conversa faria esta tela mostrar a proposta errada.
 */

"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  ChatEmptyState,
  MessageList,
  type ToolTrace,
} from "@/components/chat/messages";
import { Composer } from "@/components/chat/composer";
import { ConversationSidebar } from "@/components/conversation-list/sidebar";
import { PlanReview } from "@/components/plan-review/plan-review";
import { Button, IconAlert, IconInfo, IconX, LoadError, cx } from "@/components/ui";
import { useAnexos } from "./anexos-do-composer";
import { SobreposicaoDeArraste, useArrastarImagem } from "./arrastar-imagem";
import { RequestFailed, api } from "@/lib/api-client";
import { mensagemDeFalha } from "@/lib/resultado-do-apply";
import { subscribeToTurn, toolLabel, type StreamEvent } from "@/lib/sse";
import {
  esquecerUltimaConversa,
  guardarUltimaConversa,
  lerUltimaConversa,
} from "@/lib/ultimo-lugar";
import type {
  ApplyResult,
  CalendarRef,
  Conversation,
  Credential,
  ExternalChanges,
  KeySource,
  Me,
  Message,
  Plan,
  ProviderInfo,
  Target,
} from "@/lib/types";

interface ConversationPage {
  items: Conversation[];
  next_cursor: string | null;
}

/**
 * Quando a URL pode ser trocada para a conversa recém-criada (§8.1).
 *
 * Extraída do componente para poder ser testada sozinha: o defeito que
 * ela corrige é de SEQUÊNCIA, e sequência não se vê renderizando uma
 * vez. A regra tem de valer nos três momentos do primeiro turno, e o
 * caso perigoso é o do meio.
 *
 * `streaming` true significa turno em curso: navegar aí remonta a tela
 * (`/chat` e `/chat/[id]` são páginas diferentes), o estado do turno
 * zera e o EventSource é fechado pela limpeza do efeito.
 */
/**
 * O endereço das ações de um plano (§5.8).
 *
 * Existe como função nomeada por um motivo específico: o bug que ela
 * corrige foi usar o id ERRADO numa URL, e uma URL montada inline não
 * tem como ser testada sem subir a tela inteira e espionar a rede.
 *
 * O id do plano e o id do turno que o propôs são dois UUIDs
 * diferentes. Os dois estão no mesmo componente, a poucas linhas um do
 * outro, e um deles se chamava `planId` sem ser o id do plano. Oito
 * cliques do usuário viraram oito respostas 404.
 */
export function rotaDoPlano(plano: Plan, acao: "apply" | "reject") {
  return `/plans/${plano.id}/${acao}`;
}

/**
 * Insiste ate a proposta aparecer, com teto.
 *
 * A promocao de `ai.plan_drafts` para `core.plans` roda DENTRO do
 * `GET /plans`, e ja foi medida em 98 segundos na conta de verdade. O
 * que havia aqui era `retry: 2, retryDelay: 400`: cerca de 800 ms, e
 * foi por isso que o usuario descobriu a proposta dando F5.
 *
 * Teto, e nao laco infinito: se depois disto nao apareceu, algo
 * quebrou no worker, e girar para sempre esconderia o defeito. Nada se
 * perde, porque a consulta nao depende mais deste caminho e o cartao
 * aparece na proxima montagem da tela.
 *
 * `esperar` entra por parametro para o teste rodar instantaneo e sem
 * relogio falso.
 */
export async function insistirAteAparecer(
  buscar: () => Promise<Plan | null>,
  esperar: (ms: number) => Promise<void> = (ms) =>
    new Promise((ok) => setTimeout(ok, ms)),
  tentativas = 8,
  intervalo = 2000,
): Promise<Plan | null> {
  for (let i = 0; i < tentativas; i += 1) {
    const achado = await buscar();
    if (achado !== null) return achado;
    if (i < tentativas - 1) await esperar(intervalo);
  }
  return null;
}

/**
 * A lista de mensagens com o eco local no fim, quando houver.
 *
 * O eco e a mensagem que a pessoa acabou de enviar e que o servidor
 * ainda nao devolveu. Sem ele, apertar Enter fazia o texto digitado
 * sumir ate o turno terminar.
 *
 * Some sozinho: quando a lista do servidor ja contem uma mensagem do
 * usuario com o mesmo texto, o eco nao e adicionado, e nao ha o
 * piscar de ver a frase duas vezes.
 */
export function comEcoLocal(doServidor: Message[], eco: string | null): Message[] {
  if (eco === null) return doServidor;
  const jaChegou = doServidor.some((m) => m.role === "user" && m.content === eco);
  if (jaChegou) return doServidor;
  return [
    ...doServidor,
    {
      id: "eco-local",
      role: "user",
      content: eco,
      created_at: new Date().toISOString(),
    } as Message,
  ];
}

/**
 * A tela deve mostrar o estado vazio (os quatro exemplos)?
 *
 * Existe como funcao nomeada e exportada porque e um booleano de
 * quatro termos que decide se a SUPERFICIE DE APROVACAO e alcancavel:
 * o estado vazio renderiza NO LUGAR do `MessageList`, e o cartao de
 * proposta e filho do `MessageList`.
 *
 * O termo `temProposta` e o conserto. Numa montagem em que as
 * mensagens ainda nao chegaram (`messages.data` indefinido, logo
 * comprimento 0), a tela caia no estado vazio e o cartao ficava
 * inalcancavel mesmo com a proposta ja carregada. Era um segundo
 * caminho, independente da consulta, para o cartao nunca aparecer.
 */
export function mostraEstadoVazio(
  idAtivo: string | null,
  quantasMensagens: number,
  streaming: boolean,
  temTextoParcial: boolean,
  temProposta: boolean,
): boolean {
  if (temProposta) return false;
  if (streaming || temTextoParcial) return false;
  return idAtivo === null || quantasMensagens === 0;
}

/**
 * Qual plano proposto pertence a esta conversa.
 *
 * Mora fora do componente para poder ser testada: o cartão de
 * aprovação não apareceu numa conversa real mesmo com o plano correto
 * no banco, e a decisão de exibir ou não passava por aqui, inline,
 * onde nenhum teste alcançava.
 *
 * O filtro por conversa existe porque uma proposta pendente de OUTRA
 * conversa não é o que acabou de ser proposto aqui — mostrá-la
 * convidaria a aprovar, no meio de um assunto, algo pedido em outro.
 */
export function planoDaConversa(
  propostos: Plan[],
  idAtivo: string | null,
): Plan | null {
  if (idAtivo === null) return null;
  // A lista chega ordenada por `created_at DESC`, então o primeiro que
  // casar é o mais recente.
  return propostos.find((p) => p.conversation_id === idAtivo) ?? null;
}

export function podeNavegarParaNova(
  streaming: boolean,
  novaConversa: string | null,
  conversationId: string | null,
): boolean {
  if (streaming) return false;
  if (!novaConversa) return false;
  // Já estamos na página certa: navegar de novo seria um no-op que
  // ainda assim remonta a árvore.
  return conversationId === null;
}

export function ChatScreen({
  me,
  conversationId,
  providers,
  credentials,
  keySource,
  calendars,
  accountTarget,
}: {
  me: Me;
  conversationId: string | null;
  providers: ProviderInfo[];
  credentials: Credential[];
  /**
   * De onde vem a chave desta conta (§3451).
   *
   * Necessário porque quem usa a chave da INSTÂNCIA não tem credencial
   * própria: `credentials` vem vazio, e o seletor do composer fica em
   * `"::"`. Sem isto, o composer não sabe qual modelo vai rodar — e o
   * botão de anexo sumia justamente para quem usa o Age sem chave
   * própria, que é o caminho padrão.
   */
  keySource: KeySource | null;
  calendars: CalendarRef[];
  /** Camada 1 de §5.7.2: a preferência da conta. */
  accountTarget: Target;
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  /*
   * A credencial que REALMENTE vale: `is_active`, como `key-source` e o
   * `usable` do composer ja exigem.
   *
   * `GET /llm/credentials` devolve as inativas tambem -- e uma
   * credencial desativada vinha na frente da chave da instancia,
   * fazendo a tela anunciar um modelo que nao vai rodar. Na conta real
   * era uma chave DeepSeek desligada com o nome aposentado
   * `deepseek-chat`, e foi o que escondeu o botao de anexo.
   */
  const timezone = me.timezone;
  const credencialAtiva = credentials.find((c) => c.is_active) ?? null;

  /*
   * Os anexos vivem AQUI, nao no composer.
   *
   * A zona de arrastar e a `<main>` inteira, e as miniaturas ficam no
   * composer: com o hook dentro dele, soltar uma imagem no meio da
   * conversa cairia numa lista que ninguem mostra.
   */

  const [streaming, setStreaming] = useState(false);
  /*
   * A mensagem que acabou de ser enviada, enquanto o servidor nao a
   * devolve. Sem isto o texto digitado SOME ao apertar Enter: a lista
   * vem do banco e so e recarregada depois, entao durante toda a
   * primeira ida a rede a tela nao mostra o que a pessoa escreveu.
   */
  const [ecoLocal, setEcoLocal] = useState<string | null>(null);
  const [streamingText, setStreamingText] = useState("");
  const [traces, setTraces] = useState<ToolTrace[]>([]);
  const [applying, setApplying] = useState(false);
  const [turnError, setTurnError] = useState<string | null>(null);
  /*
   * O que acabou de ser aprovado, para oferecer desfazer.
   *
   * `/plans/<id>/undo` existe no core-api desde o M3, com janela de 7
   * dias, e a tela nunca ofereceu o botão: quem aprovava sem querer
   * tinha que apagar item por item na agenda.
   *
   * Vive no estado do turno, não numa lista persistente: desfazer é
   * reação ao que acabou de acontecer. Passou da conversa, o caminho
   * é a tela de agenda.
   */
  const [aplicado, setAplicado] = useState<{
    planId: string;
    count: number;
  } | null>(null);
  const [desfazendo, setDesfazendo] = useState(false);
  const unsubscribe = useRef<(() => void) | null>(null);

  /*
   * A conversa criada DENTRO desta tela, antes de a URL mudar.
   *
   * Relato: "na primeira mensagem o feedback não aparece, e só consigo
   * ver a resposta se der F5; a partir da segunda funciona".
   *
   * A causa era o `router.replace(/chat/<id>)` disparado assim que a
   * conversa nascia, no meio do turno. `/chat` e `/chat/[id]` são duas
   * páginas diferentes, então a troca REMONTA o ChatScreen: o
   * `useState` do streaming volta a `false`, o texto acumulado se
   * perde, e o `useEffect` de limpeza fecha o EventSource do turno que
   * estava em curso. O turno continuava no servidor e terminava bem —
   * por isso o F5 mostrava a resposta — mas a tela que o acompanhava
   * já não existia.
   *
   * A navegação agora espera o turno acabar (`done`). Até lá o id vive
   * aqui, o que mantém as queries de mensagens e de plano apontando
   * para a conversa certa sem trocar de página.
   */
  const [novaConversa, setNovaConversa] = useState<string | null>(null);
  const idAtivo = conversationId ?? novaConversa;

  // A URL alcançou o id que estava só no estado: a página certa montou
  // e a muleta não é mais necessária.
  useEffect(() => {
    if (conversationId && conversationId === novaConversa) {
      setNovaConversa(null);
    }
  }, [conversationId, novaConversa]);

  /*
   * Lembra qual conversa estava aberta, para o item "Conversa" da
   * navegação voltar para ela em vez da tela vazia (§8.1).
   *
   * Guarda só quando a conversa já tem id na URL: durante o primeiro
   * turno o id vive em `novaConversa`, e gravar ali faria a navegação
   * apontar para uma conversa que ainda pode falhar.
   */
  useEffect(() => {
    if (conversationId) guardarUltimaConversa(conversationId);
  }, [conversationId]);

  // A URL só muda depois que o turno termina. Remontar a tela aqui é
  // inofensivo: não há mais stream aberto nem texto parcial a perder, e
  // a mensagem já foi recarregada do banco.
  useEffect(() => {
    if (podeNavegarParaNova(streaming, novaConversa, conversationId)) {
      router.replace(`/chat/${novaConversa}`);
    }
  }, [streaming, novaConversa, conversationId, router]);

  // Encerra o stream ao sair da tela. Sem isto, trocar de conversa
  // deixaria o EventSource anterior vivo, e os tokens do turno antigo
  // continuariam chegando na conversa nova.
  useEffect(() => () => unsubscribe.current?.(), []);

  const conversations = useQuery({
    queryKey: ["conversations"],
    queryFn: () => api.get<ConversationPage>("/conversations", { limit: 50 }),
    // O título é gerado por um job assíncrono com `_defer_by=3`
    // (§6.2.2): no `done` do turno ele ainda não existe, e a
    // invalidação feita ali traz a conversa sem ele — depois nada mais
    // busca, e a sidebar fica "Sem título" até trocar de tela.
    //
    // A condição vive na query, e não num `setTimeout` após o `done`,
    // porque aqui ela vale para qualquer conversa sem título, não só
    // para a que acabou de ser criada: enquanto houver alguma, a lista
    // volta a buscar; quando todas têm, o polling para sozinho.
    refetchInterval: (query) =>
      query.state.data?.items.some((c) => c.title === null) ? 4000 : false,
    // Sem isto o polling pausa quando a janela perde o foco, e é
    // justamente aí que o título costuma chegar: a pessoa manda a
    // mensagem e troca de aba enquanto espera.
    refetchIntervalInBackground: true,
  });

  const messages = useQuery({
    queryKey: ["messages", idAtivo],
    queryFn: () =>
      api.get<{ items: Message[] }>(`/conversations/${idAtivo}/messages`, {
        limit: 100,
      }),
    enabled: idAtivo !== null,
  });

  const active = conversations.data?.items.find((c) => c.id === idAtivo);

  const providerEfetivo =
    active?.provider_key ??
    credencialAtiva?.provider_key ??
    (keySource?.source === "instance" ? keySource.provider_key : null) ??
    null;
  const modeloEfetivo =
    active?.model ??
    credencialAtiva?.default_model ??
    (keySource?.source === "instance" ? keySource.model : null) ??
    null;
  // A MESMA regra do composer: so arrasta para modelo que ve imagem.
  const modeloVeImagem = Boolean(
    modeloEfetivo &&
      providers
        .find((p) => p.key === providerEfetivo)
        ?.vision_models?.includes(modeloEfetivo),
  );

  const estadoDeAnexos = useAnexos();
  const [avisoDeArraste, setAvisoDeArraste] = useState<string | null>(null);

  const arraste = useArrastarImagem({
    // Sempre ligado: PDF vira texto extraído e funciona em qualquer
    // modelo. É o `onSoltar` que recusa a IMAGEM quando o modelo não
    // a vê — recusar o arraste inteiro tiraria o PDF junto.
    ativo: true,
    onSoltar: (arquivos, ignorados) => {
      // Imagem exige modelo com visão; PDF, não.
      const aceitos = modeloVeImagem
        ? arquivos
        : arquivos.filter((f) => f.type === "application/pdf");
      const imagensRecusadas = arquivos.length - aceitos.length;

      if (aceitos.length > 0) void estadoDeAnexos.adicionar(aceitos);

      if (imagensRecusadas > 0) {
        setAvisoDeArraste(
          "Este modelo não lê imagens. Escolha outro para mandar foto — PDF funciona em qualquer um.",
        );
        return;
      }
      /*
       * Soltar um PDF e nao acontecer NADA parece defeito. O aviso diz
       * o que foi ignorado e por que -- e e temporario porque uma
       * mensagem de erro permanente vira mobilia.
       */
      if (ignorados > 0) {
        setAvisoDeArraste(
          aceitos.length > 0
            ? `${ignorados} ${ignorados === 1 ? "arquivo foi ignorado" : "arquivos foram ignorados"}: aceito imagem e PDF.`
            : "Por enquanto só dá para anexar imagem (JPEG, PNG, GIF, WebP) e PDF.",
        );
      }
    },
  });

  // O aviso some sozinho. `avisoDeArraste` na dependencia e nao um
  // timer no handler: soltar duas vezes seguidas reinicia a contagem
  // em vez de o primeiro timer apagar o segundo aviso.
  useEffect(() => {
    if (!avisoDeArraste) return;
    const t = setTimeout(() => setAvisoDeArraste(null), 6000);
    return () => clearTimeout(t);
  }, [avisoDeArraste]);

  /**
   * §5.10: reativo, nunca notificação.
   *
   * Chamado ao abrir o chat, com `acknowledge=true`. A mudança externa
   * é reconhecida **uma vez** — sem isso a mesma reunião voltaria em
   * toda abertura, e o produto deixaria de ser reativo para passar a
   * perseguir o usuário.
   */
  const changes = useQuery({
    queryKey: ["external-changes"],
    queryFn: () =>
      api.get<ExternalChanges>("/calendar/external-changes", {
        acknowledge: true,
      }),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });

  /*
   * A proposta pendente DESTA conversa, buscada sempre.
   *
   * Era `enabled: turnoComPlano !== null`, e o `turnoComPlano` so era
   * preenchido num lugar: o evento `plan` do SSE. Nada o restaurava.
   * O resultado e o relato "tu nunca confirma porra nenhuma": dar F5,
   * trocar de tela, ou mandar a mensagem seguinte (que limpava o
   * estado) destruia o cartao de aprovacao para sempre, mesmo com o
   * plano vivo e `proposed` no banco, respondendo em
   * `GET /plans?status=proposed`. A tela so parava de perguntar.
   *
   * Agora a presenca do cartao e funcao do SERVIDOR, nao de um estado
   * de turno que so existe em memoria. O evento `plan` virou gatilho
   * de refetch: ele diz "olha de novo agora", nao "pode olhar".
   *
   * `status=proposed` faz o core-api promover os rascunhos pendentes
   * antes de listar, entao esta chamada e tambem o que materializa o
   * plano que o worker acabou de propor.
   */
  const plan = useQuery({
    queryKey: ["plan", "proposto-da-conversa", idAtivo],
    queryFn: async () => {
      const propostos = await api.get<Plan[]>("/plans?status=proposed");
      return planoDaConversa(propostos, idAtivo);
    },
    enabled: idAtivo !== null,
    staleTime: 10_000,
  });

  const handleEvent = useCallback(
    (event: StreamEvent) => {
      switch (event.type) {
        case "tool_start":
          setTraces((previous) => [
            ...previous,
            { name: event.name, label: toolLabel(event.name), done: false },
          ]);
          break;
        case "tool_end":
          setTraces((previous) => {
            const next = [...previous];
            // De trás para frente: a mesma tool pode rodar duas vezes
            // num turno, e marcar a primeira ocorrência deixaria a
            // segunda girando para sempre.
            for (let i = next.length - 1; i >= 0; i -= 1) {
              if (next[i].name === event.name && !next[i].done) {
                next[i] = {
                  ...next[i],
                  done: true,
                  ok: event.ok,
                  summary: event.summary,
                };
                break;
              }
            }
            return next;
          });
          break;
        case "token":
          setStreamingText((previous) => previous + event.delta);
          break;
        case "plan":
          /*
           * Gatilho, nao porta.
           *
           * A promocao de `ai.plan_drafts` para `core.plans` e preguicosa
           * e roda DENTRO do `GET /plans`, entao a primeira busca pode
           * chegar antes de o plano existir. Antes isso era fatal, porque
           * este evento era a UNICA coisa que ligava a consulta; agora a
           * consulta vive por conta propria e isto so apressa a descoberta.
           */
          void insistirAteAparecer(async () => {
            await queryClient.invalidateQueries({ queryKey: ["plan"] });
            return (
              queryClient.getQueryData<Plan | null>([
                "plan",
                "proposto-da-conversa",
                idAtivo,
              ]) ?? null
            );
          });
          break;
        case "done":
          setStreaming(false);
          // A mensagem ja esta no banco: o eco sai para nao duplicar.
          setEcoLocal(null);
          // Recarrega do banco: a mensagem persistida é a fonte, e o
          // texto acumulado no cliente era só a pré-visualização.
          void queryClient.invalidateQueries({ queryKey: ["messages"] });
          void queryClient.invalidateQueries({ queryKey: ["conversations"] });
          setStreamingText("");
          break;
        case "error":
          setStreaming(false);
          setTurnError(event.message);
          break;
      }
    },
    [queryClient],
  );

  async function send(text: string, attachmentIds: string[] = []) {
    setTurnError(null);
    // O aviso de "entrou na agenda" vale para o que acabou de
    // acontecer. Mantê-lo durante o turno seguinte o transformaria em
    // mobília, e o botão de desfazer apontaria para um plano velho.
    setAplicado(null);
    setTraces([]);
    setStreamingText("");
    /*
     * O cartao de aprovacao NAO e limpo aqui.
     *
     * Havia um `setTurnoComPlano(null)` nesta linha, e ele era um dos
     * caminhos do relato: fazer uma pergunta de acompanhamento ("e se
     * fosse mais cedo?") apagava da tela a proposta pendente, embora
     * ela continuasse viva no banco. Quem decide se o cartao fica e o
     * servidor, pelo status do plano.
     */
    /*
     * O turno COMECA aqui, antes de qualquer ida a rede.
     *
     * `setStreaming(true)` ficava depois de criar a conversa, e a
     * criacao so acontece na PRIMEIRA mensagem. O efeito era o relato:
     * "toda vez que um usuario inicia um chat novo, ele nao traz aquela
     * mensagem de feedback na parte de baixo [...] so a partir da segunda
     * mensagem que isso acontece".
     *
     * Era literal. Da segunda em diante o ramo de criar conversa e
     * pulado e o indicador aparecia no mesmo tique do clique; na
     * primeira, ele esperava duas idas a rede, e durante elas a tela
     * continuava mostrando os quatro exemplos, como se o envio nao
     * tivesse acontecido.
     */
    setStreaming(true);
    setEcoLocal(text);
    let targetId = idAtivo;
    try {
      // Primeira mensagem sem conversa: cria uma. O título vem depois,
      // automático (§6.2.2).
      if (!targetId) {
        const created = await api.post<Conversation>("/conversations", {});
        targetId = created.id;
        // Guarda em estado e NÃO navega: trocar de página agora
        // remontaria esta tela e mataria o turno que está por começar.
        // A URL é acertada no `done`.
        setNovaConversa(created.id);
        // Sem `await`: a lista de conversas da barra lateral nao e
        // requisito para o turno comecar, e esperar por ela atrasava a
        // primeira resposta por uma ida a rede inteira.
        void queryClient.invalidateQueries({ queryKey: ["conversations"] });
      }

      const accepted = await api.post<{ turn_id: string }>(
        `/conversations/${targetId}/messages`,
        {
          content: text,
          client_turn_id: crypto.randomUUID(),
          // Só vai no corpo quando há anexo: mandar `[]` em toda
          // mensagem mudaria o payload de 100% dos envios por causa de
          // um caso que é minoria.
          ...(attachmentIds.length > 0 ? { attachment_ids: attachmentIds } : {}),
        },
      );

      await queryClient.invalidateQueries({ queryKey: ["messages", targetId] });

      unsubscribe.current?.();
      unsubscribe.current = subscribeToTurn(accepted.turn_id, {
        onEvent: handleEvent,
        onClose: () => setStreaming(false),
        onError: () => {
          setStreaming(false);
          setTurnError(
            "Perdi a conexão com a resposta. O turno pode ter continuado, recarregue para ver.",
          );
        },
      });
    } catch (err) {
      setStreaming(false);
      setEcoLocal(null);
      setTurnError(
        err instanceof RequestFailed ? err.message : "Não consegui enviar a mensagem.",
      );
    }
  }

  /**
   * Recusar a proposta inteira.
   *
   * Recebe o id na renderização, quando `plan.data` comprovadamente
   * existe, em vez de ler `plan.data?.id` dentro do callback: entre o
   * clique e a chamada a consulta pode ter sido invalidada, e o
   * TypeScript estava certo em reclamar.
   *
   * O erro é exibido. Antes havia um `.catch(() => undefined)` aqui, e
   * foi ele que deixou o 404 passar despercebido oito vezes seguidas.
   */
  function rejeitarPlano(plano: Plan) {
    return async () => {
      try {
        await api.post(rotaDoPlano(plano, "reject"));
        // Quem decide se o cartao sai e o servidor: o plano passa a
        // `rejected` e deixa de aparecer na lista de propostos.
        await queryClient.invalidateQueries({ queryKey: ["plan"] });
      } catch (err) {
        setTurnError(
          err instanceof RequestFailed
            ? err.message
            : "Não consegui descartar esta proposta.",
        );
      }
    };
  }

  async function applyPlan(itemIds: string[], confirmBlocking: boolean) {
    /*
     * `plan.data.id`, NUNCA `turnoComPlano`.
     *
     * O id do plano é outro, descoberto pela consulta acima. Esta
     * linha usava o id do turno, então todo clique em "Aprovar"
     * mandava o número errado para `/plans/<id>/apply` e levava 404.
     * A variável se chamava `planId`, e o nome foi metade da causa.
     *
     * O relato foi "apareceu o pop-up para selecionar, só que não
     * funcionou, deu algum erro". No log: 8 tentativas, 8 respostas
     * 404, e a pessoa clicou oito vezes porque a tela nunca disse o
     * que havia de errado.
     */
    const plano = plan.data;
    if (!plano) {
      setTurnError(
        "Perdi a referência desta proposta. Recarregue a página para " +
          "vê-la de novo; nada foi agendado.",
      );
      return;
    }
    setApplying(true);
    try {
      const result = await api.post<ApplyResult>(rotaDoPlano(plano, "apply"), {
        item_ids: itemIds,
        confirm_blocking: confirmBlocking,
      });
      if (result.applied_count > 0) {
        setAplicado({ planId: plano.id, count: result.applied_count });
      }
      /*
       * A agenda, a lista e A PROPOSTA mudaram.
       *
       * `["plan"]` entrou aqui porque o cartao deixou de ser apagado a
       * mao: se o plano continuar `proposed` (foi o que acontece numa
       * recusa por conflito), ele VOLTA, que e o certo. Antes um
       * `setTurnoComPlano(null)` apagava o cartao mesmo quando nada
       * tinha sido aplicado.
       */
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["plan"] }),
        queryClient.invalidateQueries({ queryKey: ["plans"] }),
        queryClient.invalidateQueries({ queryKey: ["events"] }),
        queryClient.invalidateQueries({ queryKey: ["tasks"] }),
        queryClient.invalidateQueries({ queryKey: ["conversations"] }),
      ]);
      /*
       * Nada entrou tem de aparecer, SEMPRE.
       *
       * A condicao aqui era `manual_fix_needed.length > 0`, e um
       * FAILED nao popula essa lista: o caso mais comum de fracasso
       * era o unico mudo. Ver `mensagemDeFalha`.
       */
      const aviso = mensagemDeFalha(result);
      if (aviso !== null) setTurnError(aviso);
    } catch (err) {
      setTurnError(
        err instanceof RequestFailed ? err.message : "Não consegui aplicar.",
      );
    } finally {
      setApplying(false);
    }
  }

  async function mutateConversation(run: () => Promise<unknown>) {
    /*
     * Renomear, fixar, arquivar e apagar passam por aqui.
     *
     * O erro aparece em vez de desaparecer. Com o `.catch(() =>
     * undefined)` que havia antes, um rename que falhasse voltava ao
     * título antigo no refetch, sem nada explicando: parecia que o
     * clique não pegou, e a pessoa tentava de novo.
     *
     * A invalidação roda nos dois casos, de propósito: se falhou, a
     * lista volta ao estado real do servidor.
     */
    try {
      await run();
      setTurnError(null);
    } catch (err) {
      setTurnError(
        err instanceof RequestFailed
          ? err.message
          : "Não consegui salvar essa mudança na conversa.",
      );
    } finally {
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
    }
  }

  const primaryCalendar =
    calendars.find((c) => c.is_primary)?.name ??
    calendars[0]?.name ??
    "Calendário do Age";

  // §5.7.2, camadas 1 e 2: a conversa sobrepõe a conta; `null` na
  // conversa significa herdar, e é por isso que a coluna é nullable.
  const effectiveTarget: Target = active?.target_default ?? accountTarget;

  return (
    <div className="flex h-dvh">
      {/* `md:` dá 768px, onde a nav principal (240px) e esta lista
          (256px) deixam pouco para a conversa. Subir para `lg:`
          pioraria outra coisa: não há gaveta de conversas abaixo
          deste ponto, então a lista some e não há como trocar de
          conversa. A estreiteza em 768px é o mal menor até existir
          essa gaveta — anotado em PROGRESSO.md. */}
      <div className="hidden w-64 shrink-0 md:block">
        <ConversationSidebar
          conversations={conversations.data?.items ?? []}
          counts={Object.fromEntries(
            (conversations.data?.items ?? []).map((c) => [
              c.id,
              { events: c.applied_events, tasks: c.applied_tasks },
            ]),
          )}
          activeId={idAtivo}
          timezone={timezone}
          loading={conversations.isLoading}
          onNew={() => router.push("/chat")}
          onSearch={(term) => {
            void queryClient.fetchQuery({
              queryKey: ["conversations"],
              queryFn: () =>
                api.get<ConversationPage>("/conversations", {
                  limit: 50,
                  q: term || undefined,
                }),
            });
          }}
          onRename={(id, title) =>
            mutateConversation(() => api.patch(`/conversations/${id}`, { title }))
          }
          onPin={(id, pinned) =>
            mutateConversation(() =>
              api.post(`/conversations/${id}/${pinned ? "pin" : "unpin"}`),
            )
          }
          onArchive={(id) =>
            mutateConversation(() => api.post(`/conversations/${id}/archive`))
          }
          onDelete={async (id) => {
            await mutateConversation(() => api.del(`/conversations/${id}`));
            // Esquece antes de navegar: a navegação lê o valor
            // guardado, e apontar para uma conversa apagada daria tela
            // de erro no lugar do chat.
            if (id === lerUltimaConversa()) esquecerUltimaConversa();
            if (id === idAtivo) router.replace("/chat");
          }}
          onDeleteMany={async (ids) => {
            /*
             * Em série, não com `Promise.all`.
             *
             * Apagar 20 conversas de uma vez dispararia 20 requisições
             * simultâneas, e o rate limit do core-api (§2219) responde
             * 429 ao excedente: o usuário veria "apaguei tudo" com
             * metade ainda na lista. Em série é mais lento e termina.
             */
            for (const id of ids) {
              await api.del(`/conversations/${id}`).catch(() => undefined);
            }
            await queryClient.invalidateQueries({ queryKey: ["conversations"] });
            const lembrada = lerUltimaConversa();
            if (lembrada && ids.includes(lembrada)) esquecerUltimaConversa();
            if (idAtivo && ids.includes(idAtivo)) {
              router.replace("/chat");
            }
          }}
          onFork={async (id) => {
            try {
              const forked = await api.post<Conversation>(
                `/conversations/${id}/fork`,
                {},
              );
              await queryClient.invalidateQueries({ queryKey: ["conversations"] });
              router.push(`/chat/${forked.id}`);
            } catch {
              // Silencioso de propósito: ramificar é ação secundária, e
              // um alerta modal por falha nela interromperia o fluxo
              // principal sem o usuário ter perdido nada.
            }
          }}
        />
      </div>

      {/* `relative` ancora a sobreposicao de arraste no `inset-0`. */}
      <main
        className="relative flex min-w-0 flex-1 flex-col pb-14 md:pb-0"
        {...arraste.props}
      >
        <SobreposicaoDeArraste
          visivel={arraste.sobre}
          cheio={estadoDeAnexos.anexos.length >= 4}
        />

        {avisoDeArraste ? (
          <p
            role="status"
            className="mx-4 mt-3 rounded-lg bg-warn/10 px-3 py-2 text-xs text-warn ring-1 ring-inset ring-warn/20 md:mx-6"
          >
            {avisoDeArraste}
          </p>
        ) : null}

        {/* §5.10: o reconhecimento aparece uma vez, no topo, e não como
            notificação. */}
        {changes.data && changes.data.changes.length > 0 ? (
          <div
            role="status"
            className={cx(
              "flex items-start gap-2.5 border-b px-4 py-3 text-sm leading-relaxed md:px-6",
              changes.data.conflict_count > 0
                ? "border-warn/25 bg-warn/8 text-warn"
                : "border-line bg-surface-2 text-ink-2",
            )}
          >
            {changes.data.conflict_count > 0 ? (
              <IconAlert className="mt-0.5 h-4 w-4 shrink-0" />
            ) : (
              <IconInfo className="mt-0.5 h-4 w-4 shrink-0 text-muted" />
            )}
            <span className="min-w-0">{changes.data.summary_text}</span>
          </div>
        ) : null}

        {/*
         * O estado vazio sai de cena assim que o turno comeca.
         *
         * A condicao so olhava a lista de mensagens, que so e
         * recarregada quando o turno termina — entao quem mandava a
         * primeira mensagem via os quatro exemplos continuarem ali
         * durante os segundos de espera, como se o envio nao tivesse
         * acontecido.
         */}
        {mostraEstadoVazio(
          idAtivo,
          messages.data?.items.length ?? 0,
          streaming,
          streamingText !== "",
          plan.data != null,
        ) ? (
          <ChatEmptyState
            calendarName={primaryCalendar}
            onPick={(text) => void send(text)}
          />
        ) : messages.isError ? (
          <LoadError
            message="Não consegui carregar esta conversa."
            onRetry={() => void messages.refetch()}
          />
        ) : (
          <MessageList
            messages={comEcoLocal(messages.data?.items ?? [], ecoLocal)}
            streaming={streaming}
            streamingText={streamingText}
            traces={traces}
            timezone={timezone}
          >
            {plan.data && plan.data.status === "proposed" ? (
              <PlanReview
                plan={plan.data}
                timezone={timezone}
                applying={applying}
                onApply={applyPlan}
                onReject={rejeitarPlano(plan.data)}
                onReschedule={async (itemId, start, end) => {
                  await api.patch(`/plans/items/${itemId}`, { start, end });
                  // Recarrega o plano: os horários exibidos vêm do
                  // servidor, e manter o valor antigo na tela depois de
                  // salvar faria a pessoa duvidar se gravou.
                  await plan.refetch();
                }}
                onAskChange={(texto) => {
                  // Vira uma mensagem comum: o Age já sabe que a
                  // proposta está na tela, então "tira os de sábado"
                  // chega com o contexto inteiro do turno anterior.
                  void send(texto);
                }}
              />
            ) : null}
          </MessageList>
        )}

        {/*
         * Desfazer logo depois de aprovar.
         *
         * A rota `/plans/<id>/undo` existe desde o M3 e a tela nunca a
         * ofereceu: quem aprovava sem querer tinha que ir à agenda e
         * apagar item por item.
         *
         * Aparece junto do campo de escrever, que é onde o olho está
         * quando a aprovação termina, e some no primeiro envio
         * seguinte. Desfazer é reação ao que acabou de acontecer.
         */}
        {aplicado ? (
          <div className="px-4 md:px-6">
            <div
              role="status"
              className="mx-auto mb-2 flex max-w-3xl flex-wrap items-center gap-x-3 gap-y-1.5 rounded-xl bg-ok/10 px-3.5 py-2.5 text-sm ring-1 ring-inset ring-ok/25"
            >
              <span className="min-w-0 flex-1 leading-relaxed text-ink-2">
                {aplicado.count === 1
                  ? "1 item entrou na sua agenda."
                  : `${aplicado.count} itens entraram na sua agenda.`}
              </span>
              <button
                disabled={desfazendo}
                onClick={async () => {
                  setDesfazendo(true);
                  try {
                    await api.post(`/plans/${aplicado.planId}/undo`);
                    setAplicado(null);
                    await Promise.all([
                      queryClient.invalidateQueries({ queryKey: ["events"] }),
                      queryClient.invalidateQueries({ queryKey: ["tasks"] }),
                    ]);
                  } catch (err) {
                    setTurnError(
                      err instanceof RequestFailed
                        ? err.message
                        : "Não consegui desfazer.",
                    );
                  } finally {
                    setDesfazendo(false);
                  }
                }}
                className="shrink-0 font-medium text-brand transition-opacity hover:opacity-80 disabled:opacity-50"
              >
                {desfazendo ? "Desfazendo…" : "Desfazer"}
              </button>
              <button
                onClick={() => setAplicado(null)}
                aria-label="Dispensar aviso"
                className="shrink-0 text-xs text-muted transition-colors hover:text-ink"
              >
                dispensar
              </button>
            </div>
          </div>
        ) : null}

        {turnError ? (
          <div className="px-4 md:px-6">
            <div
              role="alert"
              className="mx-auto mb-2 flex max-w-3xl items-start gap-2.5 rounded-xl bg-danger/10 px-3.5 py-2.5 text-sm ring-1 ring-inset ring-danger/25"
            >
              <IconAlert className="mt-0.5 h-4 w-4 shrink-0 text-danger" />
              <span className="min-w-0 flex-1 leading-relaxed text-ink-2">
                {turnError}
              </span>
              <Button
                size="icon"
                variant="quiet"
                className="-my-1 h-7 w-7 shrink-0"
                onClick={() => setTurnError(null)}
                aria-label="Fechar aviso"
              >
                <IconX className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        ) : null}

        <Composer
          estadoDeAnexos={estadoDeAnexos}
          providers={providers}
          credentials={credentials}
          providerKey={
            active?.provider_key ??
            credencialAtiva?.provider_key ??
            // A chave da instância é o último recurso, e para a maioria
            // das contas é o ÚNICO: sem credencial ATIVA, os dois acima
            // são `undefined`.
            (keySource?.source === "instance" ? keySource.provider_key : null) ??
            null
          }
          model={
            active?.model ??
            credencialAtiva?.default_model ??
            (keySource?.source === "instance" ? keySource.model : null) ??
            null
          }
          target={effectiveTarget}
          sending={streaming}
          onSend={send}
          onProviderChange={(providerKey, model) => {
            if (!idAtivo) return;
            void mutateConversation(() =>
              api.patch(`/conversations/${idAtivo}`, {
                provider_key: providerKey,
                model,
              }),
            );
          }}
          onTargetChange={(target) => {
            if (!idAtivo) return;
            void mutateConversation(() =>
              api.patch(`/conversations/${idAtivo}`, {
                target_default: target,
              }),
            );
          }}
        />
      </main>
    </div>
  );
}
