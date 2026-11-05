"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { CampoSenha } from "@/components/auth/campo-senha";
import { Button, Field, IconAlert, Input } from "@/components/ui";
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
    <div className="animate-fade-up">
      {/* O título principal da tela, e diz de longe que aqui se ENTRA. */}
      <h1 className="text-3xl font-semibold tracking-[-0.025em]">Entrar</h1>
      <p className="mt-1.5 text-sm text-muted">Que bom te ver de novo.</p>

      {error ? (
        <p
          role="alert"
          className="mt-6 flex items-start gap-2 rounded-xl bg-danger/10 px-3.5 py-2.5 text-sm text-danger ring-1 ring-inset ring-danger/25"
        >
          <IconAlert className="mt-0.5 h-4 w-4 shrink-0" />
          {error}
        </p>
      ) : null}

      {/* `noValidate` deixa a validação para a mensagem do servidor: a
          do browser aparece num balão que o leitor de tela não anuncia
          de forma confiável. */}
      <form onSubmit={submit} noValidate className="mt-6 space-y-4">
        <Field label="E-mail" htmlFor="email">
          <Input
            id="email"
            type="email"
            name="email"
            autoComplete="email"
            inputMode="email"
            autoFocus
            required
            placeholder="voce@exemplo.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </Field>

        <Field label="Senha" htmlFor="password">
          <CampoSenha
            id="password"
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
          size="lg"
          loading={busy}
          full
          disabled={!email.trim() || !password}
        >
          Entrar
        </Button>
      </form>

      <p className="mt-8 text-center text-sm text-muted">
        Ainda não tem conta?{" "}
        <Link
          href="/register"
          className="font-semibold text-brand underline-offset-4 transition-opacity hover:underline"
        >
          Criar conta
        </Link>
      </p>
    </div>
  );
}
