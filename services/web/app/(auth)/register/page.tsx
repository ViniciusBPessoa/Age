"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { CampoSenha } from "@/components/auth/campo-senha";
import { Button, Field, IconAlert, IconCheck, Input, cx } from "@/components/ui";
import { RequestFailed, auth } from "@/lib/api-client";

/** O backend exige 10 (§9.1), e dizer isso antes evita um 422. */
const MIN_PASSWORD = 10;

export default function RegisterPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const tooShort = password.length > 0 && password.length < MIN_PASSWORD;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await auth.register({
        email: email.trim(),
        password,
        name: name.trim() || undefined,
        // O fuso vem do browser **só aqui**, no cadastro, como palpite
        // inicial — e é editável em Configurações. Daí para frente todo
        // horário usa `users.timezone` (§9.5): quem viaja não quer a
        // agenda inteira se deslocando.
        timezone:
          Intl.DateTimeFormat().resolvedOptions().timeZone || "America/Sao_Paulo",
      });
      // O core-api já entra na conta ao registrar, então vai direto
      // para o onboarding — que é o que decide se a pessoa chega a usar
      // o produto (§8.1).
      router.replace("/onboarding");
    } catch (err) {
      setError(
        err instanceof RequestFailed
          ? err.message
          : "Não consegui criar a conta. Tente de novo.",
      );
      setBusy(false);
    }
  }

  return (
    <div className="animate-fade-up">
      <h1 className="text-3xl font-semibold tracking-[-0.025em]">Criar conta</h1>
      <p className="mt-1.5 text-sm text-muted">
        Leva um minuto. Depois o Age pergunta como você estuda.
      </p>

      {error ? (
        <p
          role="alert"
          className="mt-6 flex items-start gap-2 rounded-xl bg-danger/10 px-3.5 py-2.5 text-sm text-danger ring-1 ring-inset ring-danger/25"
        >
          <IconAlert className="mt-0.5 h-4 w-4 shrink-0" />
          {error}
        </p>
      ) : null}

      <form onSubmit={submit} noValidate className="mt-6 space-y-4">
        <Field label="Como quer ser chamado" htmlFor="name" hint="Opcional.">
          <Input
            id="name"
            name="name"
            autoComplete="given-name"
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>

        <Field label="E-mail" htmlFor="email">
          <Input
            id="email"
            type="email"
            name="email"
            autoComplete="email"
            inputMode="email"
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
            autoComplete="new-password"
            required
            minLength={MIN_PASSWORD}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          {/* A regra à vista e marcada quando cumprida, em vez de um erro
              que só aparece depois de enviar. */}
          <p
            className={cx(
              "mt-1.5 flex items-center gap-1.5 text-xs transition-colors",
              password.length >= MIN_PASSWORD ? "text-ok" : "text-muted",
            )}
          >
            <IconCheck className="h-3.5 w-3.5" />
            {password.length >= MIN_PASSWORD
              ? "Tamanho bom."
              : tooShort
                ? `Pelo menos ${MIN_PASSWORD} caracteres. Faltam ${MIN_PASSWORD - password.length}.`
                : `Pelo menos ${MIN_PASSWORD} caracteres.`}
          </p>
        </Field>

        <Button
          type="submit"
          variant="primary"
          size="lg"
          loading={busy}
          full
          disabled={!email.trim() || password.length < MIN_PASSWORD}
        >
          Criar conta
        </Button>
      </form>

      <p className="mt-8 text-center text-sm text-muted">
        Já tem conta?{" "}
        <Link
          href="/login"
          className="font-semibold text-brand underline-offset-4 transition-opacity hover:underline"
        >
          Entrar
        </Link>
      </p>
    </div>
  );
}
