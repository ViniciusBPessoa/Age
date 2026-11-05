import type { NextConfig } from "next";

/*
 * Cabeçalhos de segurança, em toda resposta.
 *
 * Não havia nenhum: a auditoria encontrou só `X-Powered-By: Next.js`,
 * que anuncia a stack e não protege nada. O que cada um faz:
 *
 * - CSP: só carrega script, estilo, imagem e conexão da própria origem.
 *   `'unsafe-inline'` em script é o preço do Next sem nonce (ele injeta
 *   o estado de hidratação inline); mesmo assim a política fecha
 *   `object-src`, `base-uri`, `form-action` e `frame-ancestors`, que é
 *   onde mora o resto dos ataques de injeção. Em dev o Next precisa de
 *   `'unsafe-eval'` para o hot reload.
 * - `frame-ancestors 'none'` + `X-Frame-Options`: ninguém embute o Age
 *   num iframe para induzir clique (clickjacking).
 * - `nosniff`: o navegador não "adivinha" que um anexo é HTML.
 * - `Referrer-Policy`: o id de conversa na URL não vaza para links
 *   externos (fontes da busca na web).
 * - `Permissions-Policy`: câmera, microfone e localização desligados;
 *   o produto não usa nenhum.
 */
const IS_DEV = process.env.NODE_ENV !== "production";

const CSP = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${IS_DEV ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const SECURITY_HEADERS = [
  { key: "Content-Security-Policy", value: CSP },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
  },
  // HSTS só faz efeito sob HTTPS, e o navegador ignora em http://localhost.
  ...(IS_DEV
    ? []
    : [{ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" }]),
];

const nextConfig: NextConfig = {
  // Não anuncia a stack: `X-Powered-By: Next.js` só ajuda quem procura
  // a versão para casar com uma falha conhecida.
  poweredByHeader: false,

  async headers() {
    return [{ source: "/:path*", headers: SECURITY_HEADERS }];
  },

  // Imagem de produção pequena: sem isto, o container leva node_modules
  // inteiro (ARQUITETURA.md §11.3).
  output: "standalone",

  // O BFF usa esta variável no servidor. Não tem prefixo NEXT_PUBLIC_
  // de propósito: o browser nunca precisa alcançar o core-api.
  env: {},

  eslint: { ignoreDuringBuilds: true },
  typescript: { ignoreBuildErrors: false },
};

export default nextConfig;
