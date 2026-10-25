/**
 * O campo de mensagem e os dois controles do rodapé (§8.1).
 *
 *   ┌──────────────────────────────────────────────────────────┐
 *   │  preciso revisar o capítulo 2 até sexta                  │
 *   │  DeepSeek ▾    Ambos ▾                            [ ↑ ]  │
 *   └──────────────────────────────────────────────────────────┘
 *
 * O seletor de destino é a camada 2 do seletor de três camadas de
 * §5.7.2: conta → **conversa** → mensagem. Mudar aqui define o padrão
 * da conversa, e uma mensagem pode sobrepor ("só põe na lista") — e
 * quando sobrepõe, o chip pisca no valor usado, para a pessoa ver que
 * foi entendida sem precisar conferir o diff.
 */

"use client";

import { useEffect, useRef, useState } from "react";

import { Badge, Button, IconGlobe, IconSend, Select, cx } from "@/components/ui";
import {
  BotaoDeAnexo,
  type EstadoDeAnexos,
  MAXIMO_DE_ANEXOS,
  MiniaturasDeAnexo,
  useAnexos,
} from "./anexos-do-composer";
import type { Credential, ProviderInfo, Target } from "@/lib/types";
import { lerRascunho, limparRascunho, salvarRascunho } from "@/lib/rascunho";

const TARGET_LABEL: Record<Target, string> = {
  event: "Agenda",
  task: "Lista",
  both: "Ambos",
  ask: "Perguntar",
};

const TARGET_HINT: Record<Target, string> = {
  event: "Vai para a agenda, com hora marcada.",
  task: "Vai para a lista, sem ocupar horário.",
  both: "Tarefa na lista e bloco de tempo na agenda.",
  ask: "O assistente pergunta antes de decidir.",
};

export function Composer({
  providers,
  credentials,
  providerKey,
  model,
  target,
  /** Destino que a ÚLTIMA mensagem usou, se sobrepôs o da conversa. */
  overriddenTarget,
  disabled,
  sending,
  onSend,
  onProviderChange,
  onTargetChange,
  estadoDeAnexos,
  buscaNaWeb = true,
  conversaId = null,
}: {
  providers: ProviderInfo[];
  credentials: Credential[];
  providerKey: string | null;
  model: string | null;
  target: Target;
  overriddenTarget?: Target | null;
  disabled?: boolean;
  sending?: boolean;
  /**
   * `attachmentIds` so chega com imagem anexada; sem anexo, a
   * lista vem vazia e o caminho e o mesmo de sempre.
   */
  onSend: (text: string, attachmentIds: string[], deepSearch: boolean) => void;
  onProviderChange: (providerKey: string, model: string) => void;
  onTargetChange: (target: Target) => void;
  /**
   * O estado dos anexos, quando quem o possui é o pai.
   *
   * Existe porque a zona de arrastar é a `<main>` inteira, e uma
   * imagem solta no meio da conversa precisa cair na MESMA lista que
   * as miniaturas daqui mostram. Omitido, o composer cria o seu — o
   * que mantém o componente usável sozinho, como os testes o montam.
   */
  estadoDeAnexos?: EstadoDeAnexos;
  /**
   * Se o provedor tem busca na web (so a DeepSeek, hoje).
   *
   * `false` esconde o botao em vez de desabilita-lo: um botao
   * morto promete o que o modelo nao faz -- a mesma licao do
   * clipe aparecendo em modelo sem visao.
   */
  buscaNaWeb?: boolean;
  /**
   * A conversa deste composer, para guardar o rascunho por chat.
   *
   * `null` na conversa ainda não criada: o rascunho vai para a chave
   * `nova` e migra quando ela ganha id.
   */
  conversaId?: string | null;
}) {
  /*
   * O texto começa VAZIO e é preenchido no efeito abaixo, não em
   * `useState(() => lerRascunho(...))`.
   *
   * O motivo é render no servidor: `localStorage` não existe lá, e ler
   * no inicializador faria o HTML do servidor diferir do primeiro
   * render do cliente — o erro de hidratação do React.
   */
  const [text, setText] = useState("");
  const area = useRef<HTMLTextAreaElement>(null);
  const [pulse, setPulse] = useState(false);
  // Pesquisa aprofundada: le o conteudo das paginas (~6.500 tokens
  // contra ~1.200). Vive por MENSAGEM, nao por conversa: ligar para
  // uma pergunta e deixar ligado encareceria todo o resto.
  const [deep, setDeep] = useState(false);
  // `useAnexos` roda sempre (regra dos hooks), e o do pai vence
  // quando existe. Chamar condicionalmente quebraria a ordem dos
  // hooks entre renders.
  const proprio = useAnexos();
  const { anexos, adicionar, remover, limpar, idsProntos, subindo } =
    estadoDeAnexos ?? proprio;

  // Carrega o rascunho desta conversa, e recarrega ao trocar de
  // conversa: sem a dependência, abrir outro chat mostraria o texto
  // do anterior.
  useEffect(() => {
    setText(lerRascunho(conversaId));
  }, [conversaId]);

  // Guarda com atraso: salvar a cada tecla escreveria no disco dezenas
  // de vezes por frase. 400ms é curto o bastante para sobreviver a um
  // clique em "Agenda" logo depois de digitar.
  useEffect(() => {
    const t = setTimeout(() => salvarRascunho(conversaId, text), 400);
    return () => clearTimeout(t);
  }, [conversaId, text]);

  // O chip pisca quando a mensagem sobrepôs o padrão (§8.1).
  useEffect(() => {
    if (!overriddenTarget) return;
    setPulse(true);
    const timer = setTimeout(() => setPulse(false), 2600);
    return () => clearTimeout(timer);
  }, [overriddenTarget]);

  // Cresce com o conteúdo, até um teto. Um campo de uma linha obriga a
  // rolar dentro dele para reler o que se escreveu.
  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [text]);

  // Só credenciais que funcionam entram no seletor. Oferecer uma chave
  // que falhou na última verificação é empurrar a pessoa para um erro
  // que já é conhecido.
  const usable = providers.filter((p) =>
    credentials.some((c) => c.provider_key === p.key && c.is_active),
  );
  /*
   * O botao de anexo so existe quando o modelo ESCOLHIDO aceita
   * imagem.
   *
   * A captura da tela mostrou o botao ativo com "Mock (testes)"
   * selecionado -- o mock nao ve imagem, entao o anexo subiria, seria
   * gravado e o worker o descartaria com um `log.warning`. Oferecer
   * algo que o backend vai ignorar e a promessa que nenhum teste pega,
   * porque cada camada estava certa sozinha.
   *
   * `?? []` e nao `?? models`: backend sem o campo significa "nao sei
   * quais veem", e esconder o botao e melhor que prometer.
   */
  // O modelo que a instancia empresta, quando a conta nao tem chave
  // propria. `providerKey`/`model` ja vem resolvidos pelo chat-screen.
  const modeloDaInstancia = usable.length === 0 && providerKey && model ? model : null;

  const modeloVeImagem = Boolean(
    model &&
      providers.find((p) => p.key === providerKey)?.vision_models?.includes(model),
  );

  // Busca na web: do PROVEDOR, nao do modelo (a busca e um endpoint
  // separado, e o resultado entra como texto). Derivado do catalogo
  // aqui, como a visao acima, e nao exigido do pai: foi assim que o
  // clipe sumiu da conta real -- quem monta o componente nao sabe
  // responder por capacidade de provedor.
  //
  // `buscaNaWeb` ainda existe como prop para o teste poder forcar, mas
  // o default vem do catalogo.
  const provedorBusca = Boolean(
    providers.find((p) => p.key === providerKey)?.web_search,
  );
  const mostrarBusca = buscaNaWeb && provedorBusca;

  // PDF vira texto extraído e funciona em QUALQUER modelo; só a
  // imagem exige visão. Sem esta distinção, anexar um PDF a um modelo
  // sem visão bloquearia o envio sem razão.
  const temImagemAnexada = anexos.some((a) => !a.ehPdf);

  const broken = credentials.filter((c) => c.last_error);
  /*
   * Enquanto uma imagem sobe, o envio espera.
   *
   * Mandar sem ela faria o modelo responder a uma pergunta sobre uma
   * foto que nao chegou -- e o texto ja diria "nesta imagem". Esperar
   * alguns segundos e o comportamento honesto; o botao fica desligado e
   * a miniatura mostra o giro, entao a espera tem causa visivel.
   */
  const canSend =
    text.trim().length > 0 &&
    !disabled &&
    !sending &&
    !subindo &&
    // Com IMAGEM e modelo sem visao, enviar faria o modelo responder
    // a uma pergunta sobre uma foto que ele nao ve. PDF nao entra
    // nesta conta: ele ja chega como texto.
    (!temImagemAnexada || modeloVeImagem);

  function submit() {
    if (!canSend) return;
    onSend(text.trim(), idsProntos, deep && mostrarBusca);
    setText("");
    // O rascunho morre junto com o envio: deixá-lo marcaria a conversa
    // como "tem mensagem não enviada" logo depois de enviar.
    limparRascunho(conversaId);
    limpar();
    // Desliga depois de enviar, junto com o texto e os anexos: a
    // pesquisa aprofundada vale para a pergunta que a pessoa acabou de
    // fazer. Deixar ligado faria toda mensagem seguinte custar 5x sem
    // ela ter pedido.
    setDeep(false);
  }

  return (
    <div className="px-4 pb-4 pt-2 md:px-6">
      <div className="mx-auto max-w-3xl">
        {broken.length > 0 ? (
          <p
            role="status"
            className="mb-2 flex items-center gap-2 rounded-lg bg-warn/10 px-3 py-2 text-xs text-warn ring-1 ring-inset ring-warn/20"
          >
            A chave do {broken[0].provider_key} falhou na última verificação. Dá para
            corrigir em Configurações.
          </p>
        ) : null}

        {/*
         * Trocar para um modelo sem visao com imagem ja anexada.
         *
         * Nao removo os anexos sozinho: a pessoa pode estar voltando
         * para o modelo anterior, e apagar o trabalho dela por causa de
         * um clique no seletor e pior que avisar. O envio fica
         * bloqueado enquanto durar, porque mandar a pergunta sem a
         * imagem faria o modelo responder sobre algo que nao viu.
         */}
        {temImagemAnexada && !modeloVeImagem ? (
          <p
            role="status"
            className="mb-2 flex items-center gap-2 rounded-lg bg-warn/10 px-3 py-2 text-xs text-warn ring-1 ring-inset ring-warn/20"
          >
            Este modelo não lê imagens. Escolha outro, ou remova o anexo para enviar só
            o texto.
          </p>
        ) : null}

        {/*
         * A caixa inteira é a superfície de foco, com anel.
         *
         * `focus-within` no contêiner em vez de borda no textarea: o
         * campo e os dois seletores vivem dentro da mesma moldura, e o
         * anel precisa envolver o conjunto, não só a linha de texto.
         */}
        <div
          className={cx(
            "rounded-2xl border border-line-strong bg-surface shadow-raised",
            "transition-all duration-200",
            "focus-within:border-brand focus-within:ring-[3px] focus-within:ring-brand/20",
          )}
        >
          <MiniaturasDeAnexo anexos={anexos} onRemover={remover} />

          <label htmlFor="composer" className="sr-only">
            Mensagem
          </label>
          <textarea
            id="composer"
            ref={area}
            rows={1}
            value={text}
            disabled={disabled}
            placeholder="Peça algo, ou conte o que precisa organizar…"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              // Enter envia, Shift+Enter quebra linha — a convenção que
              // todo chat usa, e contrariá-la custa uma mensagem
              // enviada por engano.
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            className="w-full resize-none bg-transparent px-4 pt-3.5 text-base leading-relaxed text-ink outline-none placeholder:text-faint disabled:opacity-50"
          />

          <div className="flex flex-wrap items-center gap-1.5 px-2.5 pb-2.5 pt-1">
            {/*
             * O botao aparece SEMPRE: PDF vira texto extraido e
             * funciona em qualquer modelo. Esconde-lo quando o modelo
             * nao ve imagem tiraria o PDF junto -- e PDF e o caso que
             * vale para o `mock` e para qualquer provedor futuro.
             *
             * A imagem continua protegida: `aceitos` filtra na hora de
             * escolher, e o aviso explica.
             */}
            <BotaoDeAnexo
              onEscolher={(arquivos) => {
                const aceitos = modeloVeImagem
                  ? arquivos
                  : arquivos.filter((f) => f.type === "application/pdf");
                if (aceitos.length > 0) void adicionar(aceitos);
              }}
              disabled={disabled}
              cheio={anexos.length >= MAXIMO_DE_ANEXOS}
              soPdf={!modeloVeImagem}
            />

            {/* Seletor de provedor/modelo (§3.1.3: trocar afeta a
                próxima mensagem, a conversa segue com o mesmo
                histórico). */}
            <label htmlFor="provider-select" className="sr-only">
              Provedor e modelo
            </label>
            <Select
              bare
              id="provider-select"
              value={`${providerKey ?? ""}::${model ?? ""}`}
              disabled={usable.length === 0}
              onChange={(e) => {
                const [key, chosen] = e.target.value.split("::");
                onProviderChange(key, chosen);
              }}
            >
              {usable.length === 0 ? (
                /*
                 * "sem credencial" era mentira para quem usa a chave da
                 * INSTANCIA: a conta nao tem credencial propria, mas
                 * conversa normalmente -- e a captura mostrou esse
                 * rotulo ao lado de um botao de anexo que funciona.
                 *
                 * Sem credencial E sem instancia, ai sim nao da para
                 * conversar, e o texto antigo vale.
                 */
                <option value="::">
                  {modeloDaInstancia
                    ? `${modeloDaInstancia} · incluído`
                    : "sem credencial"}
                </option>
              ) : null}
              {usable.map((provider) =>
                provider.models.map((candidate) => (
                  <option
                    key={`${provider.key}::${candidate}`}
                    value={`${provider.key}::${candidate}`}
                  >
                    {provider.name} · {candidate}
                  </option>
                )),
              )}
            </Select>

            {/*
             * Pesquisa aprofundada.
             *
             * `aria-pressed` num `<button>`, e nao `<label>`+checkbox:
             * jsdom nao propaga clique de label, entao um teste do
             * segundo caminho passaria sem testar nada (foi o que
             * aconteceu no teste do clipe).
             */}
            {mostrarBusca ? (
              <Button
                variant={deep ? "primary" : "ghost"}
                size="icon"
                className="rounded-xl"
                aria-pressed={deep}
                onClick={() => setDeep((v) => !v)}
                title={
                  deep
                    ? "Pesquisa aprofundada ligada: entra nos sites e le as paginas"
                    : "Pesquisar na internet e ler as paginas (gasta mais)"
                }
                aria-label="Pesquisa aprofundada na internet"
              >
                <IconGlobe />
              </Button>
            ) : null}

            <span aria-hidden className="h-4 w-px bg-line" />

            {/* Seletor de destino (§5.7.2). */}
            <label htmlFor="target-select" className="sr-only">
              Para onde vai o que for proposto
            </label>
            <Select
              bare
              id="target-select"
              value={target}
              title={TARGET_HINT[target]}
              onChange={(e) => onTargetChange(e.target.value as Target)}
              className={cx(pulse && "age-pulse rounded-lg")}
            >
              {(Object.keys(TARGET_LABEL) as Target[]).map((value) => (
                <option key={value} value={value}>
                  {TARGET_LABEL[value]}
                </option>
              ))}
            </Select>

            {/* Quando a mensagem sobrepôs, dizer qual valor valeu. O
                `aria-live` faz o leitor de tela anunciar — piscar
                sozinho não comunica nada para quem não vê. */}
            {overriddenTarget && overriddenTarget !== target ? (
              <span aria-live="polite">
                <Badge tone="brand">
                  esta foi para {TARGET_LABEL[overriddenTarget].toLowerCase()}
                </Badge>
              </span>
            ) : null}

            <Button
              variant="primary"
              size="icon"
              className="ml-auto rounded-xl"
              onClick={submit}
              loading={sending}
              disabled={!canSend}
              aria-label="Enviar mensagem"
            >
              {sending ? null : <IconSend />}
            </Button>
          </div>
        </div>

        {/*
         * A dica inteira so no desktop.
         *
         * Em 390px ela quebra em duas linhas e encosta na barra de
         * navegacao de baixo, que e fixa: a segunda linha sai cortada.
         * No celular fica a parte que a pessoa precisa saber (nada
         * entra sem aprovar); "Enter envia" nao vale a linha extra em
         * quem digita com teclado virtual.
         */}
        <p className="mt-2 text-center text-2xs text-faint">
          <span className="hidden sm:inline">
            Enter envia · Shift+Enter quebra linha ·{" "}
          </span>
          nada é escrito na sua agenda sem você aprovar
        </p>
      </div>
    </div>
  );
}
