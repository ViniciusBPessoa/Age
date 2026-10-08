import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Age",
  description: "Assistente de agenda conversacional",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="pt-BR">
      <body>{children}</body>
    </html>
  );
}
