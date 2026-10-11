import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";

import { Providers } from "@/components/providers";

import "./globals.css";

/*
 * Inter pelo `next/font`, não por `<link>` ao Google.
 *
 * O `next/font` baixa o arquivo no build e serve do nosso domínio: sem
 * requisição a terceiro em runtime (que é dado de navegação vazando
 * para fora, num produto cujo discurso é privacidade — §6.2.5), e sem
 * o salto de layout que uma fonte externa provoca ao chegar depois.
 */
const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
});

export const metadata: Metadata = {
  title: "Age",
  description: "Assistente de agenda conversacional",
  icons: { icon: "/icon.svg" },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#100f0d" },
    { media: "(prefers-color-scheme: light)", color: "#faf9f5" },
  ],
  // `maximum-scale` deliberadamente ausente: travar o zoom é uma falha
  // de acessibilidade comum em app mobile, e quem precisa ampliar texto
  // perde a única saída que tem.
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="pt-BR" className={inter.variable}>
      <body className="min-h-dvh">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
