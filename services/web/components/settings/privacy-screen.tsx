/**
 * Configurações → Privacidade (§6.2.5, §9.6).
 *
 * "Separada do resto, porque são ações destrutivas."
 *
 * ## Os três limites não são digitados aqui
 *
 * Eles vêm de `GET /privacy/limits`. O backend os mantém em constantes
 * de módulo com uma nota explícita sobre por quê: "o texto vive aqui
 * para não divergir entre a tela e a resposta da API — e para que
 * mudá-lo seja uma decisão, não um descuido". Copiar as frases para
 * dentro do componente desfaria exatamente isso, e a divergência
 * apareceria num lugar onde ela é grave: um aviso de privacidade que a
 * tela afirma e a API nega.
 *
 * ## A pré-visualização vem antes da confirmação
 *
 * `GET /privacy/deletion-preview` diz quantos itens saem **e o que
 * fica**. Mostrar o que fica é o que diferencia uma exclusão informada
 * de um clique em "apagar tudo" seguido de arrependimento — e o número
 * mais importante da tela é `will_delete.events = 0`, porque a dúvida
 * real de quem aperta "apagar histórico" é se a agenda vai junto.
 *
 * ## Apagar conta exige digitar o e-mail
 *
 * É a única ação do produto que faz isso, e é proporcional ao dano de
 * um clique errado. A validação é do backend; aqui a caixa só fica
 * desabilitada antes, para não convidar a tentativa.
 */

"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";

import {
  Button,
  Dialog,
  Field,
  Input,
  LoadError,
  Panel,
  Spinner,
} from "@/components/ui";
import { RequestFailed, api } from "@/lib/api-client";
import { dateOf } from "@/lib/format";
import type { DeletionPreview, PrivacyLimits } from "@/lib/types";

export function PrivacyScreen({
  email,
  timezone,
}: {
  email: string;
  timezone: string;
}) {
  const router = useRouter();
  const [confirmHistory, setConfirmHistory] = useState(false);
  const [confirmAccount, setConfirmAccount] = useState(false);
  const [typedEmail, setTypedEmail] = useState("");
  const [understood, setUnderstood] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const limits = useQuery({
    queryKey: ["privacy", "limits"],
    queryFn: () => api.get<PrivacyLimits>("/privacy/limits"),
  });

  const preview = useQuery({
    queryKey: ["privacy", "preview", "history"],
    queryFn: () =>
      api.get<DeletionPreview>("/privacy/deletion-preview", { scope: "history" }),
  });

  const accountPreview = useQuery({
    queryKey: ["privacy", "preview", "account"],
    queryFn: () =>
      api.get<DeletionPreview>("/privacy/deletion-preview", { scope: "account" }),
  });

  const deleteHistory = useMutation({
    mutationFn: () => api.post<{ message: string }>("/privacy/delete-history"),
    onSuccess: (result) => {
      setConfirmHistory(false);
      setNotice(result.message);
      void preview.refetch();
    },
    onError: () => setError("Não consegui apagar o histórico. Tente de novo."),
  });

  const deleteAccount = useMutation({
    mutationFn: () =>
      api.post<{ message: string }>("/privacy/delete-account", {
        confirm_email: typedEmail.trim(),
        understood_external_events: understood,
      }),
    onSuccess: () => {
      // A conta foi encerrada: o token para de valer na hora (a
      // checagem está em `get_db`), então insistir na sessão daria 401
      // em tudo. Vai para o login com a sessão limpa.
      router.replace("/login");
    },
    onError: (err) =>
      setError(
        err instanceof RequestFailed ? err.message : "Não consegui encerrar a conta.",
      ),
  });

  return (
    <div className="min-h-dvh pb-14 md:pb-0">
      <header className="border-b border-[var(--border)] px-4 py-3">
        <h1 className="text-sm font-semibold">Privacidade</h1>
      </header>

      <div className="mx-auto max-w-2xl space-y-6 px-4 py-5">
        {notice ? (
          <Panel className="border-[var(--ok)] px-4 py-3">
            <p role="status" className="text-sm">
              {notice}
            </p>
          </Panel>
        ) : null}

        {error ? (
          <Panel className="border-[var(--danger)] px-4 py-3">
            <p role="alert" className="text-sm text-[var(--danger)]">
              {error}
            </p>
          </Panel>
        ) : null}

        {/* --- Histórico --- */}
        <section>
          <h2 className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
            Histórico de conversas
          </h2>

          <Panel className="mt-2 space-y-4 px-4 py-4">
            {preview.isError ? (
              <LoadError
                message="Não consegui contar seus dados."
                onRetry={() => void preview.refetch()}
              />
            ) : preview.isLoading ? (
              <p className="text-xs text-[var(--muted)]">
                <Spinner /> contando…
              </p>
            ) : preview.data ? (
              <>
                <p className="text-sm">
                  {preview.data.will_delete.conversations} conversas ·{" "}
                  {preview.data.will_delete.messages} mensagens
                </p>

                <div className="flex flex-wrap gap-2">
                  {/* Download direto, não `fetch` + blob: o navegador
                      já sabe baixar, e passar o JSON pela memória do JS
                      só para recriar o download teria um teto de
                      tamanho que o export não precisa ter. */}
                  <a
                    href="/api/privacy/export"
                    download="age-export.json"
                    className="inline-flex items-center rounded-md border border-[var(--border-strong)] px-3.5 py-2 text-sm hover:bg-[var(--panel-2)]"
                  >
                    Exportar tudo (JSON)
                  </a>
                  <Button variant="danger" onClick={() => setConfirmHistory(true)}>
                    Apagar todo o histórico
                  </Button>
                </div>

                {/* O que FICA. É a informação que a pessoa procura antes
                    de apertar o botão. */}
                <div className="rounded-md border border-[var(--border)] bg-[var(--panel-2)] px-3 py-2.5">
                  <p className="text-xs font-medium">Apagar conversas NÃO apaga:</p>
                  <ul className="mt-1 space-y-0.5">
                    {preview.data.will_preserve.map((item) => (
                      <li key={item} className="text-xs text-[var(--muted)]">
                        · {item}
                      </li>
                    ))}
                  </ul>
                  <p className="mt-1.5 text-xs text-[var(--muted)]">
                    Só o diálogo sai. Dá para desfazer até{" "}
                    {dateOf(preview.data.undo_until, timezone)}.
                  </p>
                </div>
              </>
            ) : null}
          </Panel>
        </section>

        {/* --- Os limites honestos --- */}
        <section>
          <h2 className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
            O que apagar aqui não alcança
          </h2>

          {limits.data ? (
            <ul className="mt-2 space-y-2">
              {[limits.data.llm, limits.data.backup, limits.data.external_events].map(
                (text) => (
                  <Panel as="li" key={text} className="px-4 py-3">
                    <p className="text-xs leading-relaxed">
                      <span aria-hidden className="mr-1.5 text-[var(--warn)]">
                        ⚠
                      </span>
                      {text}
                    </p>
                  </Panel>
                ),
              )}
            </ul>
          ) : (
            <p className="mt-2 text-xs text-[var(--muted)]">
              <Spinner /> carregando…
            </p>
          )}
        </section>

        {/* --- Conta --- */}
        <section>
          <h2 className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
            Conta
          </h2>

          <Panel className="mt-2 space-y-3 px-4 py-4">
            <p className="text-xs leading-relaxed text-[var(--muted)]">
              Encerrar a conta agenda a exclusão com{" "}
              {limits.data?.undo_window_days ?? 30} dias de janela — entrar de novo
              dentro desse prazo cancela. Depois disso, não tem volta.
            </p>

            {accountPreview.data ? (
              <p className="text-xs text-[var(--muted)]">
                Sai tudo: {accountPreview.data.will_delete.conversations} conversas,{" "}
                {accountPreview.data.will_delete.events} eventos,{" "}
                {accountPreview.data.will_delete.tasks} tarefas,{" "}
                {accountPreview.data.will_delete.routines} rotinas.
                {accountPreview.data.external_events_created > 0 ? (
                  <>
                    {" "}
                    Os {accountPreview.data.external_events_created} eventos que o Age
                    criou no seu calendário externo <strong>ficam</strong>.
                  </>
                ) : null}
              </p>
            ) : null}

            <Button variant="danger" onClick={() => setConfirmAccount(true)}>
              Encerrar minha conta
            </Button>
          </Panel>
        </section>
      </div>

      {/* --- Confirmação: histórico --- */}
      <Dialog
        open={confirmHistory}
        onClose={() => setConfirmHistory(false)}
        title="Apagar todo o histórico de conversas?"
        footer={
          <>
            <Button variant="quiet" onClick={() => setConfirmHistory(false)}>
              Cancelar
            </Button>
            <Button
              variant="danger"
              loading={deleteHistory.isPending}
              onClick={() => deleteHistory.mutate()}
            >
              Apagar {preview.data?.will_delete.conversations ?? ""} conversas
            </Button>
          </>
        }
      >
        <p className="leading-relaxed">
          Sua agenda, tarefas, rotinas e planos de estudo continuam como estão. Só o
          diálogo sai, e dá para desfazer por {limits.data?.undo_window_days ?? 30}{" "}
          dias.
        </p>
      </Dialog>

      {/* --- Confirmação: conta --- */}
      <Dialog
        open={confirmAccount}
        onClose={() => {
          setConfirmAccount(false);
          setTypedEmail("");
          setUnderstood(false);
        }}
        title="Encerrar a conta"
        footer={
          <>
            <Button
              variant="quiet"
              onClick={() => {
                setConfirmAccount(false);
                setTypedEmail("");
              }}
            >
              Cancelar
            </Button>
            <Button
              variant="danger"
              loading={deleteAccount.isPending}
              disabled={typedEmail.trim().toLowerCase() !== email.toLowerCase()}
              onClick={() => deleteAccount.mutate()}
            >
              Encerrar
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <p className="leading-relaxed">
            Isto agenda a exclusão de tudo: conversas, agenda, tarefas, rotinas e
            planos. Você tem {limits.data?.undo_window_days ?? 30} dias para mudar de
            ideia entrando de novo.
          </p>

          <Field
            label={`Digite ${email} para confirmar`}
            htmlFor="confirm-email"
            hint="É a única ação do produto que pede isto. O motivo é o tamanho do estrago de um clique errado."
          >
            <Input
              id="confirm-email"
              type="email"
              autoComplete="off"
              value={typedEmail}
              onChange={(e) => setTypedEmail(e.target.value)}
            />
          </Field>

          {(accountPreview.data?.external_events_created ?? 0) > 0 ? (
            <label className="flex items-start gap-2 text-xs leading-relaxed">
              <input
                type="checkbox"
                checked={understood}
                onChange={(e) => setUnderstood(e.target.checked)}
                className="mt-0.5 accent-[var(--warn)]"
              />
              <span>
                Entendi que os {accountPreview.data?.external_events_created} eventos
                que o Age criou no meu calendário externo não serão apagados — eles são
                meus e estão na minha agenda.
              </span>
            </label>
          ) : null}
        </div>
      </Dialog>
    </div>
  );
}
