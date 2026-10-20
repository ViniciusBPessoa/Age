/**
 * Configurações (§8.1).
 *
 * "Perfil e timezone · Credenciais de LLM · Conexões de calendário e de
 * tarefas · Preferências de agendamento · Destino padrão (§5.7.2) ·
 * Sessões ativas · Uso e custo estimado."
 *
 * O destino padrão é a **camada 1** do seletor de três camadas de
 * §5.7.2 — a preferência da conta, que a conversa pode sobrepor e a
 * mensagem pode sobrepor de novo. Deixá-lo aqui, e não só no rodapé do
 * chat, é o que torna a precedência visível: dá para ver qual é o
 * padrão sem abrir uma conversa.
 *
 * Privacidade fica em `/settings/privacy`, em página própria, porque
 * são ações destrutivas e §8.1 as separa de propósito.
 */

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import {
  Badge,
  Button,
  Field,
  IconCalendar,
  IconChevronRight,
  IconShield,
  IconTasks,
  IconTrash,
  Input,
  LoadError,
  PageHeader,
  Panel,
  Select,
  Spinner,
} from "@/components/ui";
import { RequestFailed, api, auth } from "@/lib/api-client";
import { moment } from "@/lib/format";
import { presentationFor } from "@/lib/providers";
import { useMostrarCusto } from "@/lib/usar-custo";
import type {
  AutonomyLevel,
  CalendarRef,
  Credential,
  KeySource,
  Me,
  Preferences,
  ProviderInfo,
  SessionInfo,
  Target,
  TaskListRef,
  VerifyResult,
} from "@/lib/types";

const TARGET_LABEL: Record<Target, string> = {
  event: "Agenda: com hora marcada",
  task: "Lista: sem ocupar horário",
  both: "Ambos: tarefa e bloco de tempo",
  ask: "Perguntar a cada vez",
};

const AUTONOMY_LABEL: Record<AutonomyLevel, string> = {
  propose: "Sempre propor e esperar aprovação",
  auto_create: "Criar sozinho o que não tem conflito",
  auto_all: "Criar e remanejar sozinho",
};

export function SettingsScreen({ me }: { me: Me }) {
  return (
    <div className="min-h-dvh pb-16 md:pb-0">
      <PageHeader title="Configurações" width="2xl" />

      <div className="mx-auto max-w-2xl space-y-8 px-4 py-6 md:px-7">
        <ProfileSection me={me} />
        <CredentialsSection />
        <ConnectionsSection />
        <SchedulingSection />
        <SessionsSection />
        <PrivacyLink />
      </div>
    </div>
  );
}

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <section>
      <h2 className="text-base font-semibold">{title}</h2>
      {description ? (
        <p className="mt-1 text-xs leading-relaxed text-muted">{description}</p>
      ) : null}
      <div className="mt-2">{children}</div>
    </section>
  );
}

// ---------------------------------------------------------------
function ProfileSection({ me }: { me: Me }) {
  const router = useRouter();
  const [name, setName] = useState(me.name ?? "");
  const [timezone, setTimezone] = useState(me.timezone);
  const [saved, setSaved] = useState(false);

  const save = useMutation({
    mutationFn: () =>
      api.patch<Me>("/auth/me", { name: name.trim() || null, timezone }),
    onSuccess: () => {
      setSaved(true);
      // `refresh()` e não só estado local: o fuso atravessa TODA a
      // interface (§9.5), e as telas o recebem do servidor. Sem
      // recarregar, a agenda continuaria desenhada no fuso antigo.
      router.refresh();
      setTimeout(() => setSaved(false), 2500);
    },
  });

  // A lista do browser, que é a fonte autoritativa de nomes de fuso
  // disponíveis no ambiente — datilografar "America/Sao_Paulo" errado
  // é fácil, e o backend recusaria com um 422 pouco explicativo.
  const zones =
    typeof Intl.supportedValuesOf === "function"
      ? Intl.supportedValuesOf("timeZone")
      : [me.timezone, "America/Sao_Paulo", "UTC"];

  return (
    <Section title="Perfil">
      <Panel className="space-y-3 px-4 py-4">
        <Field label="Nome" htmlFor="s-name">
          <Input
            id="s-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={120}
          />
        </Field>

        <Field
          label="Fuso horário"
          htmlFor="s-tz"
          hint="Todo horário que o Age mostra e calcula usa este fuso."
        >
          <Select
            id="s-tz"
            value={timezone}
            onChange={(e) => setTimezone(e.target.value)}
          >
            {zones.map((zone) => (
              <option key={zone} value={zone}>
                {zone}
              </option>
            ))}
          </Select>
        </Field>

        <p className="text-xs text-[var(--muted)]">{me.email}</p>

        <div className="flex items-center gap-3">
          <Button
            variant="primary"
            size="sm"
            loading={save.isPending}
            onClick={() => save.mutate()}
          >
            Salvar
          </Button>
          {saved ? (
            <span role="status" className="text-xs font-medium text-ok">
              salvo
            </span>
          ) : null}
        </div>
      </Panel>
    </Section>
  );
}

// ---------------------------------------------------------------
function CredentialsSection() {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [providerKey, setProviderKey] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState<string | null>(null);

  const providers = useQuery({
    queryKey: ["providers"],
    queryFn: () => api.get<ProviderInfo[]>("/llm/providers"),
  });

  const credentials = useQuery({
    queryKey: ["credentials"],
    queryFn: () => api.get<Credential[]>("/llm/credentials"),
  });

  // De qual chave este usuário depende hoje, e quanto da cota sobrou
  // (§3451). Sem isto a pessoa que usa a chave da casa bate num limite
  // invisível: a mensagem falha e nada na tela tinha avisado.
  const keySource = useQuery({
    queryKey: ["key-source"],
    queryFn: () => api.get<KeySource>("/llm/key-source"),
  });

  // A porta única do dinheiro (§12): a barra de cota mostra valor em
  // dólar, então passa pela mesma chave que o resto.
  const mostrarCusto = useMostrarCusto();

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ["credentials"] });
    // A origem da chave MUDA quando uma credencial é criada ou apagada —
    // é exatamente o que decide entre cota e sem cota. Sem invalidar
    // aqui, a tela continuaria dizendo "uso incluído" depois de a
    // pessoa colar a própria chave.
    void queryClient.invalidateQueries({ queryKey: ["key-source"] });
  }

  const upsert = useMutation({
    mutationFn: async () => {
      const created = await api.post<Credential>("/llm/credentials", {
        provider_key: providerKey,
        api_key: apiKey.trim(),
      });
      return api.post<VerifyResult>(`/llm/credentials/${created.id}/verify`);
    },
    onSuccess: (result) => {
      if (!result.ok) {
        setError(result.message ?? "A chave não funcionou.");
        refresh();
        return;
      }
      setAdding(false);
      setApiKey("");
      setError(null);
      refresh();
    },
    onError: (err) =>
      setError(err instanceof RequestFailed ? err.message : "Não consegui salvar."),
  });

  const verify = useMutation({
    mutationFn: (id: string) => api.post<VerifyResult>(`/llm/credentials/${id}/verify`),
    onSuccess: refresh,
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.del(`/llm/credentials/${id}`),
    onSuccess: refresh,
  });

  return (
    <Section
      title="Credenciais de LLM"
      description={
        keySource.data?.source === "instance"
          ? "O uso já está incluído. A chave própria é opcional e remove o limite. Qualquer chave que você colar é cifrada antes de ir para o banco e nunca volta numa resposta da API."
          : "Sua chave, seu custo. O Age não cobra nada e não vê suas conversas. A chave é cifrada antes de ir para o banco e nunca volta numa resposta da API."
      }
    >
      <div className="space-y-2">
        {/* O estado da cota, quando é a casa que paga. Vem antes da
            lista de credenciais porque é a informação que explica por
            que a lista pode estar vazia e o chat funcionar mesmo
            assim. */}
        {keySource.data?.source === "instance" ? (
          <Panel className="px-4 py-3.5">
            <p className="text-sm font-medium">Uso incluído</p>
            <p className="mt-1 text-xs leading-relaxed text-muted">
              Você está usando o modelo fornecido pelo Age
              {keySource.data.model ? ` (${keySource.data.model})` : ""}, sem precisar
              de chave própria.
              {/* Sem esta frase a tela se contradiz: "não precisa de
                  chave" com uma chave listada logo abaixo. Ela só
                  aparece quando há chave desativada, porque é só aí que
                  a aparente contradição existe. */}
              {credentials.data?.some((c) => !c.is_active)
                ? " A chave que você cadastrou está desativada — por isso não está sendo usada."
                : ""}
            </p>
            {keySource.data.limit_usd && mostrarCusto ? (
              <>
                {/* Barra de progresso com `<progress>` nativo: leitor de
                    tela anuncia o valor sozinho, e o rótulo em texto
                    fica ao lado para quem lê a tela. */}
                <div className="mt-3 flex items-center gap-3">
                  <progress
                    className="h-1.5 w-full overflow-hidden rounded-full [&::-moz-progress-bar]:bg-brand [&::-webkit-progress-bar]:bg-surface-3 [&::-webkit-progress-value]:bg-brand"
                    value={keySource.data.used_usd ?? 0}
                    max={keySource.data.limit_usd}
                  />
                  <span className="tnum shrink-0 text-xs text-muted">
                    US$ {(keySource.data.used_usd ?? 0).toFixed(2)} de US${" "}
                    {keySource.data.limit_usd.toFixed(2)}
                  </span>
                </div>
                <p className="mt-2 text-xs leading-relaxed text-faint">
                  A cota renova no dia 1º. Para não ter limite, cole sua própria chave
                  abaixo.
                </p>
              </>
            ) : null}
          </Panel>
        ) : null}

        {credentials.isError ? (
          <LoadError
            message="Não consegui carregar suas credenciais."
            onRetry={() => void credentials.refetch()}
          />
        ) : null}

        {credentials.data?.map((credential) => {
          const info = presentationFor(credential.provider_key);
          return (
            <Panel key={credential.id} className="px-4 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium">
                  {providers.data?.find((p) => p.key === credential.provider_key)
                    ?.name ?? credential.provider_key}
                </span>
                <span className="text-xs text-[var(--muted)]">
                  ••••{credential.key_last4}
                </span>
                {/* `is_active` vem na API desde sempre e era ignorado
                    aqui, o que produzia uma tela que se contradiz: o
                    painel acima diz "sem precisar de chave própria" e
                    logo abaixo aparece uma chave, sem nada explicando
                    que ela está desligada. Medido na conta do usuário:
                    `is_active = false`, e nenhum sinal na tela.

                    Vem antes dos outros selos porque "desativada"
                    explica por que a chave existe e não está em uso —
                    e "não verificada" numa chave desligada é ruído. */}
                {!credential.is_active ? (
                  <Badge tone="neutral">desativada</Badge>
                ) : credential.last_error ? (
                  <Badge tone="danger">falhou</Badge>
                ) : credential.last_verified_at ? (
                  <Badge tone="ok">verificada</Badge>
                ) : (
                  <Badge tone="warn">não verificada</Badge>
                )}
                {credential.default_model ? (
                  <span className="text-xs text-muted">{credential.default_model}</span>
                ) : null}
              </div>

              {credential.last_error ? (
                <p className="mt-1 text-xs text-danger">{credential.last_error}</p>
              ) : null}

              <div className="mt-2 flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  variant="secondary"
                  loading={verify.isPending && verify.variables === credential.id}
                  onClick={() => verify.mutate(credential.id)}
                >
                  verificar
                </Button>
                {info.keyUrl ? (
                  <a
                    href={info.keyUrl}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="text-xs font-medium text-brand hover:underline"
                  >
                    gerenciar no provedor →
                  </a>
                ) : null}
                <Button
                  size="sm"
                  variant="ghost"
                  className="ml-auto text-danger hover:bg-danger/10 hover:text-danger"
                  onClick={() => remove.mutate(credential.id)}
                >
                  <IconTrash className="h-3.5 w-3.5" />
                  remover
                </Button>
              </div>
            </Panel>
          );
        })}

        {adding ? (
          <Panel className="space-y-3 px-4 py-4">
            <Field label="Provedor" htmlFor="c-provider">
              <Select
                id="c-provider"
                value={providerKey}
                onChange={(e) => setProviderKey(e.target.value)}
              >
                <option value="">escolha…</option>
                {providers.data?.map((provider) => (
                  <option key={provider.key} value={provider.key}>
                    {provider.name}
                  </option>
                ))}
              </Select>
            </Field>

            <Field label="Chave" htmlFor="c-key" error={error}>
              <Input
                id="c-key"
                type="password"
                autoComplete="off"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
              />
            </Field>

            <div className="flex gap-2">
              <Button
                variant="primary"
                size="sm"
                loading={upsert.isPending}
                disabled={!providerKey || apiKey.trim().length < 8}
                onClick={() => upsert.mutate()}
              >
                Salvar e verificar
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setAdding(false)}>
                Cancelar
              </Button>
            </div>
          </Panel>
        ) : (
          <Button size="sm" onClick={() => setAdding(true)}>
            + Adicionar credencial
          </Button>
        )}
      </div>
    </Section>
  );
}

// ---------------------------------------------------------------
function ConnectionsSection() {
  const calendars = useQuery({
    queryKey: ["calendars"],
    queryFn: () => api.get<CalendarRef[]>("/calendar/calendars"),
  });

  const lists = useQuery({
    queryKey: ["task-lists"],
    queryFn: () => api.get<TaskListRef[]>("/tasks/lists"),
  });

  return (
    <Section title="Conexões">
      <div className="space-y-2">
        {(calendars.data ?? []).map((calendar) => (
          <Panel
            key={calendar.id}
            className="flex flex-wrap items-center gap-2 px-4 py-3"
          >
            <span className="flex items-center gap-2 text-sm font-medium">
              <IconCalendar className="text-muted" />
              {calendar.name}
            </span>
            {calendar.is_primary ? <Badge>principal</Badge> : null}
            {calendar.writable ? (
              <Badge tone="ok">leitura e escrita</Badge>
            ) : (
              <Badge tone="warn">só leitura</Badge>
            )}
          </Panel>
        ))}

        {(lists.data ?? []).map((list) => (
          <Panel key={list.id} className="flex flex-wrap items-center gap-2 px-4 py-3">
            <span className="flex items-center gap-2 text-sm font-medium">
              <IconTasks className="text-muted" />
              {list.name}
            </span>
            {list.is_default ? <Badge>padrão</Badge> : null}
          </Panel>
        ))}

        {/* Dito na cara em vez de um botão que não funciona: §8.1 põe
            "conectar Google →" na tela, mas o adaptador foi adiado de
            propósito (a suíte de contrato existe e o espera). Um botão
            morto seria pior que a ausência honesta. */}
        <Panel className="px-4 py-3">
          <p className="text-sm">Google Calendar e Google Tasks</p>
          <p className="mt-1 text-xs leading-relaxed text-[var(--muted)]">
            Ainda não nesta versão. A agenda e a lista do Age já fazem tudo o que o
            produto descreve, e quando o Google entrar, o que você criou continua onde
            está.
          </p>
        </Panel>
      </div>
    </Section>
  );
}

// ---------------------------------------------------------------
function SchedulingSection() {
  const queryClient = useQueryClient();
  const [saved, setSaved] = useState(false);

  const preferences = useQuery({
    queryKey: ["preferences"],
    queryFn: () => api.get<Preferences>("/preferences"),
  });

  const save = useMutation({
    mutationFn: (patch: Partial<Preferences>) =>
      api.put<Preferences>("/preferences", patch),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["preferences"] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    },
  });

  if (preferences.isLoading) {
    return (
      <Section title="Agendamento">
        <p className="text-xs text-[var(--muted)]">
          <Spinner /> carregando…
        </p>
      </Section>
    );
  }

  const current = preferences.data;
  if (!current) {
    return (
      <Section title="Agendamento">
        <LoadError
          message="Não consegui carregar suas preferências."
          onRetry={() => void preferences.refetch()}
        />
      </Section>
    );
  }

  return (
    <Section title="Agendamento">
      <Panel className="space-y-3 px-4 py-4">
        {/* Camada 1 de §5.7.2. */}
        <Field
          label="Destino padrão"
          htmlFor="p-target"
          hint="A conversa pode mudar isto, e uma mensagem pode mudar de novo. Este é o ponto de partida."
        >
          <Select
            id="p-target"
            value={current.default_target}
            onChange={(e) => save.mutate({ default_target: e.target.value as Target })}
          >
            {(Object.keys(TARGET_LABEL) as Target[]).map((value) => (
              <option key={value} value={value}>
                {TARGET_LABEL[value]}
              </option>
            ))}
          </Select>
        </Field>

        <Field
          label="Autonomia"
          htmlFor="p-autonomy"
          hint="Quanto o Age pode fazer sem perguntar."
        >
          <Select
            id="p-autonomy"
            value={current.autonomy_level}
            onChange={(e) =>
              save.mutate({ autonomy_level: e.target.value as AutonomyLevel })
            }
          >
            {(Object.keys(AUTONOMY_LABEL) as AutonomyLevel[]).map((value) => (
              <option key={value} value={value}>
                {AUTONOMY_LABEL[value]}
              </option>
            ))}
          </Select>
        </Field>

        <div className="grid grid-cols-2 gap-3">
          <Field label="Sessão padrão (min)" htmlFor="p-session">
            <Input
              id="p-session"
              type="number"
              min={15}
              max={180}
              defaultValue={current.session_min}
              onBlur={(e) => {
                const value = Number(e.target.value);
                if (value !== current.session_min) save.mutate({ session_min: value });
              }}
            />
          </Field>

          <Field label="Sessão máxima (min)" htmlFor="p-session-max">
            <Input
              id="p-session-max"
              type="number"
              min={15}
              max={300}
              defaultValue={current.session_max}
              onBlur={(e) => {
                const value = Number(e.target.value);
                if (value !== current.session_max) save.mutate({ session_max: value });
              }}
            />
          </Field>

          <Field label="Pausa entre sessões (min)" htmlFor="p-break">
            <Input
              id="p-break"
              type="number"
              min={0}
              max={60}
              defaultValue={current.break_min}
              onBlur={(e) => {
                const value = Number(e.target.value);
                if (value !== current.break_min) save.mutate({ break_min: value });
              }}
            />
          </Field>

          <Field label="Máximo por dia (h)" htmlFor="p-cap">
            <Input
              id="p-cap"
              type="number"
              min={1}
              max={16}
              defaultValue={current.max_hours_per_day}
              onBlur={(e) => {
                const value = Number(e.target.value);
                if (value !== current.max_hours_per_day) {
                  save.mutate({ max_hours_per_day: value });
                }
              }}
            />
          </Field>
        </div>

        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={current.spaced_repetition_enabled}
            onChange={(e) =>
              save.mutate({ spaced_repetition_enabled: e.target.checked })
            }
            className="h-4 w-4 cursor-pointer accent-brand"
          />
          Revisão espaçada nos planos de estudo
        </label>

        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            checked={current.private_mode}
            onChange={(e) => save.mutate({ private_mode: e.target.checked })}
            className="mt-0.5 h-4 w-4 cursor-pointer accent-brand"
          />
          <span>
            Modo privado
            <span className="mt-0.5 block text-xs text-[var(--muted)]">
              Manda só o necessário ao provedor de LLM: títulos de evento viram
              &ldquo;ocupado&rdquo; (§9.3).
            </span>
          </span>
        </label>

        {saved ? (
          <p role="status" className="text-xs font-medium text-ok">
            salvo
          </p>
        ) : null}
      </Panel>
    </Section>
  );
}

// ---------------------------------------------------------------
function SessionsSection() {
  const router = useRouter();
  const queryClient = useQueryClient();

  const sessions = useQuery({
    queryKey: ["sessions"],
    queryFn: () => api.get<SessionInfo[]>("/auth/sessions"),
  });

  const revoke = useMutation({
    mutationFn: (id: string) => api.del(`/auth/sessions/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["sessions"] }),
  });

  return (
    <Section title="Sessões ativas">
      <div className="space-y-2">
        {sessions.data?.map((session) => (
          <Panel
            key={session.id}
            className="flex flex-wrap items-center gap-2 px-4 py-3"
          >
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm">
                {session.device_label ?? "Dispositivo desconhecido"}
                {session.is_current ? (
                  <span className="ml-2 text-xs font-medium text-ok">este</span>
                ) : null}
              </p>
              <p className="text-xs text-muted">
                {session.ip ?? "sem IP"} · criada {moment(session.created_at, "UTC")}
              </p>
            </div>
            {!session.is_current ? (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => revoke.mutate(session.id)}
              >
                encerrar
              </Button>
            ) : null}
          </Panel>
        ))}

        <Button
          size="sm"
          variant="secondary"
          onClick={async () => {
            await auth.logout().catch(() => undefined);
            router.replace("/login");
          }}
        >
          Sair desta sessão
        </Button>
      </div>
    </Section>
  );
}

function PrivacyLink() {
  return (
    <Section title="Privacidade">
      <Link
        href="/settings/privacy"
        className="card card-hover group flex items-center gap-3.5 px-4 py-4"
      >
        <span
          aria-hidden
          className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-surface-2 text-muted ring-1 ring-inset ring-line"
        >
          <IconShield className="h-[18px] w-[18px]" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium">Exportar e apagar dados</span>
          <span className="mt-0.5 block text-xs leading-relaxed text-muted">
            Export completo, apagar histórico, encerrar a conta — e o que cada uma
            dessas coisas não alcança.
          </span>
        </span>
        <IconChevronRight className="shrink-0 text-muted transition-transform group-hover:translate-x-0.5" />
      </Link>
    </Section>
  );
}
