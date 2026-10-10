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
  IconAlert,
  IconCheck,
  IconDownload,
  Input,
  LoadError,
  PageHeader,
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
    <div className="min-h-dvh pb-16 md:pb-0">
      <PageHeader
        title="Privacidade"
        subtitle="O que sai, o que fica, e o que não volta."
        width="2xl"
      />

      <div className="mx-auto max-w-2xl space-y-8 px-4 py-6 md:px-7">
        {notice ? (
          <p
            role="status"
            className="flex items-start gap-2.5 rounded-xl bg-ok/10 px-4 py-3 text-sm leading-relaxed ring-1 ring-inset ring-ok/25"
          >
            <IconCheck className="mt-0.5 h-4 w-4 shrink-0 text-ok" />
            {notice}
          </p>
        ) : null}

        {error ? (
          <p
            role="alert"
            className="flex items-start gap-2.5 rounded-xl bg-danger/10 px-4 py-3 text-sm leading-relaxed text-danger ring-1 ring-inset ring-danger/25"
          >
            <IconAlert className="mt-0.5 h-4 w-4 shrink-0" />
            {error}
          </p>
        ) : null}

        {/* --- Histórico --- */}
        <section>
          <h2 className="text-base font-semibold">Histórico de conversas</h2>

          <Panel className="mt-2 space-y-4 px-4 py-4">
            {preview.isError ? (
              <LoadError
                message="Não consegui contar seus dados."
                onRetry={() => void preview.refetch()}
              />
            ) : preview.isLoading ? (
              <p className="flex items-center gap-2 text-xs text-muted">
                <Spinner /> contando…
              </p>
            ) : preview.data ? (
              <>
                {/* Os números grandes: é o que a pessoa confere antes
                    de apertar um botão irreversível. */}
                <div className="flex gap-8">
                  <span>
                    <span className="tnum block text-2xl font-semibold leading-none">
                      {preview.data.will_delete.conversations}
                    </span>
                    <span className="mt-1 block text-xs text-muted">conversas</span>
                  </span>
                  <span>
                    <span className="tnum block text-2xl font-semibold leading-none">
                      {preview.data.will_delete.messages}
                    </span>
                    <span className="mt-1 block text-xs text-muted">mensagens</span>
                  </span>
                </div>

                <div className="flex flex-wrap gap-2">
                  {/* Download direto, não `fetch` + blob: o navegador
                      já sabe baixar, e passar o JSON pela memória do JS
                      só para recriar o download teria um teto de
                      tamanho que o export não precisa ter. */}
                  <a
                    href="/api/privacy/export"
                    download="age-export.json"
                    className="inline-flex h-10 items-center gap-2 rounded-lg border border-line-strong bg-surface-2 px-4 text-sm font-medium shadow-ring transition-colors hover:border-muted/50 hover:bg-surface-3"
                  >
                    <IconDownload />
                    Exportar tudo (JSON)
                  </a>
                  <Button variant="danger" onClick={() => setConfirmHistory(true)}>
                    Apagar todo o histórico
                  </Button>
                </div>

                {/* O que FICA. É a informação que a pessoa procura antes
                    de apertar o botão. */}
                <div className="rounded-xl bg-surface-2 px-4 py-3.5">
                  <p className="text-xs font-semibold">Apagar conversas NÃO apaga:</p>
                  <ul className="mt-2 space-y-1">
                    {preview.data.will_preserve.map((item) => (
                      <li
                        key={item}
                        className="flex items-start gap-2 text-xs leading-relaxed text-muted"
                      >
                        <IconCheck className="mt-0.5 h-3 w-3 shrink-0 text-ok" />
                        {item}
                      </li>
                    ))}
                  </ul>
                  <p className="mt-2.5 border-t border-line pt-2.5 text-xs leading-relaxed text-muted">
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
          <h2 className="text-base font-semibold">O que apagar aqui não alcança</h2>

          {limits.data ? (
            <ul className="mt-2 space-y-2">
              {[limits.data.llm, limits.data.backup, limits.data.external_events].map(
                (text) => (
                  <Panel as="li" key={text} className="px-4 py-3">
                    <p className="text-xs leading-relaxed">
                      <span aria-hidden className="mr-1.5 text-[var(--warn)]">
                        <IconAlert className="h-3.5 w-3.5 shrink-0" />
                      </span>
                      {text}
                    </p>
                  </Panel>
                ),
              )}
            </ul>
          ) : (
            <p className="mt-2 text-xs text-muted">
              <Spinner /> carregando…
            </p>
          )}
        </section>

        {/* --- Conta --- */}
        <section>
          <h2 className="text-base font-semibold">Conta</h2>

          <Panel className="mt-2 space-y-3 px-4 py-4">
            <p className="text-xs leading-relaxed text-[var(--muted)]">
              Encerrar a conta agenda a exclusão com{" "}
              {limits.data?.undo_window_days ?? 30} dias de janela — entrar de novo
              dentro desse prazo cancela. Depois disso, não tem volta.
            </p>

            {accountPreview.data ? (
              <p className="text-xs text-muted">
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
            <Button variant="ghost" onClick={() => setConfirmHistory(false)}>
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
              variant="ghost"
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
