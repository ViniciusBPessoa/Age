/**
 * As três decisões de segurança do BFF, num lugar testável.
 *
 * O catch-all `app/api/[...path]/route.ts` e o repasse de login
 * (`lib/auth-proxy.ts`) eram corretos no caminho feliz e frouxos nas
 * bordas. A auditoria achou três furos, todos reproduzidos com `curl`
 * antes de corrigir:
 *
 * 1. **Travessia de caminho.** O Next decodifica `%2F` dentro de um
 *    segmento, então `/api/x/..%2Fauth%2Flogin` chegava aqui como
 *    `["x", "../auth/login"]`. O `new URL()` normalizava o `..` e o
 *    pedido alcançava QUALQUER rota do core-api: `/openapi.json`,
 *    `/internal/*` e, o pior, `auth/login` pelo catch-all, que devolvia
 *    o `access_token` no corpo direto para o JavaScript da página. A
 *    trava `TOKEN_BEARING` comparava a string antes da normalização.
 *
 * 2. **IP forjável.** O BFF repassava o `X-Forwarded-For` que o próprio
 *    navegador mandou, e o Next só preenche esse header quando ele NÃO
 *    vem (`??=`). Com um IP inventado por tentativa, o limite de login
 *    por IP (20 em 15 min) deixava passar tentativas sem fim.
 *
 * 3. **Cabeçalhos e corpo sem limite.** Todo header do cliente
 *    atravessava (inclusive `x-age-user-id`, que as rotas internas
 *    leem), e o corpo era lido inteiro na memória, de qualquer tamanho.
 */

/**
 * Cabeçalhos do cliente que podem chegar ao core-api. Lista FECHADA:
 * header novo precisa ser adicionado aqui de propósito.
 *
 * `cookie` e `authorization` nunca entram: quem autentica no core-api é
 * o Bearer montado pelo BFF a partir do cookie httpOnly (§4.1.1).
 */
export const CABECALHOS_PERMITIDOS = new Set([
  "accept",
  "accept-language",
  "content-type",
  "if-match",
  "if-none-match",
  "idempotency-key",
  "last-event-id",
  "user-agent",
]);

/** Teto do corpo repassado. O maior caso legítimo é o anexo de 5 MB. */
export const CORPO_MAXIMO = 8 * 1024 * 1024;

/**
 * Monta a URL de destino no core-api, ou `null` se o caminho tentar
 * sair de `/api/v1/`.
 *
 * Recusa em vez de "limpar": um segmento `..`, `.`, vazio, ou com barra
 * ou contrabarra embutida não aparece em nenhuma chamada legítima do
 * front, então quem manda isso está sondando. Cada segmento é
 * re-codificado, e o resultado é conferido depois da normalização do
 * `URL`, que é a única verificação que não depende de prever todos os
 * truques de codificação.
 */
export function destinoSeguro(path: string[], base: string): URL | null {
  if (path.length === 0) return null;
  for (const segmento of path) {
    if (
      segmento === "" ||
      segmento === "." ||
      segmento === ".." ||
      segmento.includes("/") ||
      segmento.includes("\\") ||
      // NUL e controles não têm uso legítimo num caminho de API.
      // eslint-disable-next-line no-control-regex
      /[\u0000-\u001f]/.test(segmento)
    ) {
      return null;
    }
  }
  const caminho = "/api/v1/" + path.map(encodeURIComponent).join("/");
  const destino = new URL(caminho, base);
  if (!destino.pathname.startsWith("/api/v1/")) return null;
  return destino;
}

/** O caminho canônico, para comparar com listas de rotas especiais. */
export function caminhoCanonico(path: string[]): string {
  return path.join("/").toLowerCase();
}

const IPV4 = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;
const IPV6 = /^[0-9a-f:.]+$/i;

/** Aceita só o que parece endereço IP; o resto vira `null`. */
export function ipValido(valor: string | null | undefined): string | null {
  if (!valor) return null;
  let v = valor.trim();
  // IPv4 mapeado em IPv6 (`::ffff:1.2.3.4`), como o Node reporta.
  if (v.toLowerCase().startsWith("::ffff:") && IPV4.test(v.slice(7))) v = v.slice(7);
  if (IPV4.test(v)) return v;
  if (v.includes(":") && v.length <= 45 && IPV6.test(v)) return v.toLowerCase();
  return null;
}

/**
 * O IP real do cliente, lido de trás para frente.
 *
 * O `X-Forwarded-For` acumula da esquerda para a direita, e só as
 * entradas da DIREITA foram escritas por quem confiamos. Por isso o IP
 * do cliente é o que está `proxiesConfiaveis` posições antes do fim:
 *
 *   sem proxy (0):   "<socket>"                     -> socket
 *   Caddy na frente: "<cliente>, <ip do caddy>"     -> cliente
 *
 * Quem garante que a última entrada é o socket é o
 * `scripts/ip-do-cliente.cjs`, carregado antes do Next: ele sobrescreve
 * o header que o Next deixaria passar intacto.
 *
 * Ler a primeira entrada, como antes, é ler o que o atacante escreveu.
 */
export function ipDoCliente(
  xff: string | null | undefined,
  proxiesConfiaveis = 0,
): string | null {
  if (!xff) return null;
  const partes = xff
    .split(",")
    .map((p) => p.trim())
    .filter(Boolean);
  if (partes.length === 0) return null;
  const indice = Math.max(0, partes.length - 1 - Math.max(0, proxiesConfiaveis));
  return ipValido(partes[indice]);
}

/** Quantos proxies de confiança estão na frente do Next (env). */
export function proxiesConfiaveis(): number {
  const n = Number.parseInt(process.env.AGE_TRUSTED_PROXY_HOPS ?? "0", 10);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

/** Copia só os cabeçalhos permitidos. */
export function cabecalhosParaRepasse(origem: Headers): Headers {
  const saida = new Headers();
  origem.forEach((valor, chave) => {
    if (CABECALHOS_PERMITIDOS.has(chave.toLowerCase())) saida.set(chave, valor);
  });
  return saida;
}

/** Trace vindo do cliente só é aceito se tiver formato de id. */
export function traceSeguro(valor: string | null): string {
  if (valor && /^[A-Za-z0-9_-]{8,64}$/.test(valor)) return valor;
  return crypto.randomUUID();
}

/**
 * Lê o corpo com teto. `null` quando passa do limite.
 *
 * Confere o `Content-Length` primeiro (barato, e recusa sem ler nada) e
 * depois conta o que de fato chegou, porque o header pode mentir ou
 * faltar (envio em pedaços).
 */
export async function lerCorpoLimitado(
  req: Request,
  limite = CORPO_MAXIMO,
): Promise<ArrayBuffer | null> {
  const declarado = Number(req.headers.get("content-length") ?? "0");
  if (Number.isFinite(declarado) && declarado > limite) return null;
  if (!req.body) return new ArrayBuffer(0);

  const leitor = req.body.getReader();
  const pedacos: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await leitor.read();
    if (done) break;
    total += value.byteLength;
    if (total > limite) {
      await leitor.cancel();
      return null;
    }
    pedacos.push(value);
  }
  const corpo = new Uint8Array(total);
  let pos = 0;
  for (const p of pedacos) {
    corpo.set(p, pos);
    pos += p.byteLength;
  }
  return corpo.buffer;
}

export function corpoGrandeDemais(trace: string): Response {
  return Response.json(
    {
      error: {
        code: "payload_too_large",
        message: "O envio passou do tamanho máximo permitido (8 MB).",
      },
    },
    { status: 413, headers: { "x-trace-id": trace } },
  );
}

export function caminhoInvalido(trace: string): Response {
  return Response.json(
    { error: { code: "not_found", message: "Rota não encontrada." } },
    { status: 404, headers: { "x-trace-id": trace } },
  );
}
