import type { NextConfig } from "next";

const nextConfig: NextConfig = {
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
