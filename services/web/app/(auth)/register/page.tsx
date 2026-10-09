"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button, Field, Input, Panel } from "@/components/ui";
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
    <Panel className="p-6">
      <h1 className="mb-5 text-base font-semibold">Criar conta</h1>

      <form onSubmit={submit} noValidate className="space-y-4">
        <Field label="Nome" htmlFor="name" hint="Opcional.">
          <Input
            id="name"
            name="name"
            autoComplete="name"
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
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </Field>

        <Field
          label="Senha"
          htmlFor="password"
          hint={`Pelo menos ${MIN_PASSWORD} caracteres.`}
          error={
            error ?? (tooShort ? `Faltam ${MIN_PASSWORD - password.length}.` : null)
          }
        >
          <Input
            id="password"
            type="password"
            name="password"
            autoComplete="new-password"
            required
            minLength={MIN_PASSWORD}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>

        <Button
          type="submit"
          variant="primary"
          loading={busy}
          className="w-full"
          disabled={!email.trim() || password.length < MIN_PASSWORD}
        >
          Criar conta
        </Button>
      </form>

      <p className="mt-5 text-xs text-[var(--muted)]">
        Já tem conta?{" "}
        <Link href="/login" className="text-[var(--accent)] hover:underline">
          Entrar
        </Link>
      </p>
    </Panel>
  );
}
