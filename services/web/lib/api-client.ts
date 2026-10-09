/**
 * Chamada ao backend de dentro de um Client Component.
 *
 * Sempre pelo BFF (`/api/...`), nunca direto: o browser não tem o token
 * nem alcança o core-api (§2.4.1). O cookie viaja sozinho porque é
 * same-origin.
 *
 * O que este módulo adiciona a um `fetch` cru:
 *   - desembrulha o erro do formato `{detail:{error:{code,message}}}`
 *     do FastAPI, para o componente não repetir isso em cada tela;
 *   - converte 401 num erro reconhecível, para o app mandar ao login.
 */

import type { ApiError } from "@/lib/types";

export class RequestFailed extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly retryable = false,
  ) {
    super(message);
    this.name = "RequestFailed";
  }

  get isAuth(): boolean {
    return this.status === 401;
  }
}

type Query = Record<string, string | number | boolean | undefined | null>;

function withQuery(path: string, query?: Query): string {
  if (!query) return `/api${path}`;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null) params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `/api${path}?${qs}` : `/api${path}`;
}

async function parseError(res: Response): Promise<RequestFailed> {
  const body = (await res.json().catch(() => null)) as
    | (ApiError & { detail?: ApiError })
    | null;
  const err = body?.detail?.error ?? body?.error;
  return new RequestFailed(
    res.status,
    err?.code ?? "unknown",
    err?.message ?? "Algo falhou. Tente de novo.",
    err?.retryable ?? false,
  );
}

async function send<T>(
  method: string,
  path: string,
  options: { body?: unknown; query?: Query } = {},
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(withQuery(path, options.query), {
      method,
      headers:
        options.body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    });
  } catch {
    throw new RequestFailed(
      0,
      "network",
      "Sem conexão com o servidor. Verifique sua internet.",
      true,
    );
  }

  if (!res.ok) throw await parseError(res);

  // 204 e 202 sem corpo são respostas válidas em várias rotas (apagar,
  // arquivar, reordenar). `res.json()` num corpo vazio lança.
  if (res.status === 204 || res.headers.get("content-length") === "0") {
    return undefined as T;
  }
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export const api = {
  get: <T>(path: string, query?: Query) => send<T>("GET", path, { query }),
  post: <T>(path: string, body?: unknown, query?: Query) =>
    send<T>("POST", path, { body, query }),
  patch: <T>(path: string, body?: unknown) => send<T>("PATCH", path, { body }),
  put: <T>(path: string, body?: unknown) => send<T>("PUT", path, { body }),
  del: <T>(path: string) => send<T>("DELETE", path),
};

/** Login/logout/registro passam por `/api/auth/*`, fora do catch-all. */
export const auth = {
  login: (email: string, password: string) =>
    send<{ ok: true }>("POST", "/auth/login", { body: { email, password } }),
  register: (body: {
    email: string;
    password: string;
    name?: string;
    timezone?: string;
  }) => send<{ ok: true }>("POST", "/auth/register", { body }),
  logout: () => send<{ ok: true }>("POST", "/auth/logout", {}),
};
