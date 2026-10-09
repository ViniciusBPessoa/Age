/**
 * Leitura do core-api de dentro de um Server Component.
 *
 * Por que não passar pelo BFF HTTP: um Server Component já roda no
 * servidor do Next, então chamar `/api/...` significaria o Next fazendo
 * uma requisição HTTP para si mesmo antes de falar com o core-api — um
 * salto a mais, e um que precisa do próprio servidor estar aceitando
 * conexão durante o render. Daqui a chamada sai direto, com o token
 * lido do cookie.
 *
 * Isto continua sendo BFF e não viola §2.4.1: anexa credencial,
 * repassa, devolve. Nenhuma regra de agendamento, nenhum banco, nenhum
 * provedor.
 *
 * O que este módulo NÃO faz é renovar token. Server Component não pode
 * escrever cookie (o Next proíbe `cookies().set()` durante render), e
 * renovar sem poder gravar o resultado significaria renovar em todo
 * render — queimando o refresh e, com rotação estrita, derrubando a
 * sessão. Então 401 aqui é tratado como "não autenticado" e vira
 * redirect para o login; a renovação acontece no BFF (§4.1.2), que é
 * chamado pelo cliente e pode gravar cabeçalho.
 */

import { cookies } from "next/headers";

import { ACCESS_COOKIE, CORE_API_URL, REFRESH_COOKIE } from "@/lib/session";

/** Lançado quando não há sessão válida. As páginas convertem em redirect. */
export class Unauthenticated extends Error {
  constructor() {
    super("sem sessão");
    this.name = "Unauthenticated";
  }
}

/** Lançado quando o core-api respondeu erro. Carrega o código da API. */
export class ApiCallError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiCallError";
  }
}

async function authHeaders(): Promise<HeadersInit> {
  const jar = await cookies();
  const token = jar.get(ACCESS_COOKIE)?.value;
  if (!token) {
    // Sem access mas com refresh é o caso normal depois de 15 min
    // parado. Quem resolve é o cliente, chamando o BFF — que renova e
    // grava. Aqui só dizemos que esta renderização não tem credencial.
    throw new Unauthenticated();
  }
  return { Authorization: `Bearer ${token}` };
}

/** Há alguma chance de sessão? Usado pelo layout para decidir redirect. */
export async function hasSessionCookie(): Promise<boolean> {
  const jar = await cookies();
  return Boolean(jar.get(ACCESS_COOKIE)?.value ?? jar.get(REFRESH_COOKIE)?.value);
}

/**
 * GET no core-api, tipado.
 *
 * `cache: "no-store"` em tudo: cada resposta é específica de um
 * usuário, e cachear resposta por usuário num cache compartilhado é
 * como se vaza a agenda de alguém para outra pessoa. O RLS impede no
 * banco (§4.2), mas um cache de HTTP ficaria do lado de fora dele.
 */
export async function apiGet<T>(
  path: string,
  init?: { searchParams?: Record<string, string | number | boolean | undefined> },
): Promise<T> {
  const url = new URL(`/api/v1${path}`, CORE_API_URL);
  for (const [key, value] of Object.entries(init?.searchParams ?? {})) {
    if (value !== undefined) url.searchParams.set(key, String(value));
  }

  let res: Response;
  try {
    res = await fetch(url, {
      headers: await authHeaders(),
      cache: "no-store",
    });
  } catch {
    throw new ApiCallError(
      503,
      "upstream_unavailable",
      "O serviço está indisponível. Tente em alguns segundos.",
    );
  }

  if (res.status === 401) throw new Unauthenticated();

  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as {
      error?: { code?: string; message?: string };
      detail?: { error?: { code?: string; message?: string } };
    } | null;
    // O FastAPI embrulha `detail`; o core-api põe `{error:{...}}` dentro.
    const err = body?.detail?.error ?? body?.error;
    throw new ApiCallError(
      res.status,
      err?.code ?? "unknown",
      err?.message ?? "Algo falhou ao buscar seus dados.",
    );
  }

  return (await res.json()) as T;
}

/**
 * Como `apiGet`, mas devolve `null` em vez de lançar quando o recurso
 * simplesmente não existe ou a chamada falhou.
 *
 * Serve para dado acessório de uma tela: se as mudanças externas não
 * carregarem, a timeline ainda deve aparecer. Uma tela que morre
 * inteira porque um painel lateral falhou é pior que uma tela com um
 * painel vazio (§5.9.13).
 */
export async function apiGetOptional<T>(
  path: string,
  init?: { searchParams?: Record<string, string | number | boolean | undefined> },
): Promise<T | null> {
  try {
    return await apiGet<T>(path, init);
  } catch (err) {
    if (err instanceof Unauthenticated) throw err;
    return null;
  }
}
