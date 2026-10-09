"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button, Field, Input, Panel } from "@/components/ui";
import { RequestFailed, auth } from "@/lib/api-client";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await auth.login(email.trim(), password);
      // `replace`, não `push`: o botão "voltar" não deve levar de volta
      // ao formulário de login de uma sessão já aberta.
      router.replace("/chat");
    } catch (err) {
      setError(
        err instanceof RequestFailed
          ? err.message
          : "Não consegui entrar. Tente de novo.",
      );
      setBusy(false);
    }
  }

  return (
    <Panel className="p-6">
      <h1 className="mb-5 text-base font-semibold">Entrar</h1>

      {/* `noValidate` deixa a validação para a mensagem do servidor: a
          do browser aparece num balão que o leitor de tela não anuncia
          de forma confiável. */}
      <form onSubmit={submit} noValidate className="space-y-4">
        <Field label="E-mail" htmlFor="email">
          <Input
            id="email"
            type="email"
            name="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </Field>

        <Field label="Senha" htmlFor="password" error={error}>
          <Input
            id="password"
            type="password"
            name="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>

        <Button
          type="submit"
          variant="primary"
          loading={busy}
          className="w-full"
          disabled={!email.trim() || !password}
        >
          Entrar
        </Button>
      </form>

      <p className="mt-5 text-xs text-[var(--muted)]">
        Ainda não tem conta?{" "}
        <Link href="/register" className="text-[var(--accent)] hover:underline">
          Criar uma
        </Link>
      </p>
    </Panel>
  );
}
