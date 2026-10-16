/**
 * Os três passos do onboarding (§8.1).
 *
 *   1. Modelo   — de onde vem a inteligência. Obrigatório.
 *   2. Agenda   — onde escrever. SALTÁVEL, porque `LocalCalendar` é o
 *                 default (§3.2.1) e dá para usar o produto sem OAuth.
 *   3. Pronto   — o que dizer primeiro.
 *
 * Duas decisões que valem explicação:
 *
 * **O botão do passo 2 é "deixar para depois", não "pular".** §8.1 é
 * explícito: "pular" sugere que a pessoa perdeu algo e que a decisão é
 * final. "Depois" diz a verdade — o Age já funciona com a agenda
 * local, e conectar o Google é opcional e reversível.
 *
 * **A chave é verificada antes de deixar avançar.** `POST
 * /llm/credentials/{id}/verify` faz uma chamada real ao provedor.
 * Descobrir que a chave está errada na primeira mensagem é pior:
 * naquele momento a pessoa não sabe se o erro é a chave, o produto ou
 * o que ela escreveu.
 */

"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { Logo } from "@/components/app-nav";
import {
  Badge,
  Button,
  Field,
  IconCalendar,
  IconCheck,
  IconChevronLeft,
  IconKey,
  IconTasks,
  Input,
  Panel,
  Select,
  cx,
} from "@/components/ui";
import { RequestFailed, api } from "@/lib/api-client";
import { brl } from "@/lib/format";
import {
  HIDDEN_IN_ONBOARDING,
  HIGHLIGHT_LABEL,
  presentationFor,
} from "@/lib/providers";
import type {
  CalendarRef,
  Credential,
  KeySource,
  Me,
  ProviderInfo,
  TaskListRef,
  VerifyResult,
} from "@/lib/types";

const STEPS = ["Modelo", "Agenda", "Pronto"] as const;

export function OnboardingFlow({
  me,
  providers,
  initialCredentials,
  calendars,
  taskLists,
  keySource,
}: {
  me: Me;
  providers: ProviderInfo[] | null;
  initialCredentials: Credential[];
  calendars: CalendarRef[];
  taskLists: TaskListRef[];
  keySource: KeySource | null;
}) {
  const router = useRouter();
  const [step, setStep] = useState(Math.min(me.onboarding_step, 2));
  const [credentials, setCredentials] = useState(initialCredentials);

  // Avança e grava, para retomar no mesmo lugar se a pessoa sair.
  async function goTo(next: number) {
    setStep(next);
    try {
      await api.patch<Me>("/auth/me", { onboarding_step: next });
    } catch {
      // Falhar aqui não deve travar o fluxo: o passo é conveniência de
      // retomada, não dado crítico. A pessoa continua pela tela.
    }
  }

  async function finish() {
    try {
      await api.patch<Me>("/auth/me", { onboarding_step: 3 });
    } catch {
      // Mesma razão acima.
    }
    router.replace("/chat");
  }

  return (
    <main className="aurora mx-auto w-full max-w-xl px-5 py-10 md:py-14">
      <div className="mb-8 flex items-center gap-2.5">
        <Logo />
        <span className="text-base font-semibold tracking-[-0.02em]">Age</span>
      </div>

      <Stepper current={step} />

      {step === 0 ? (
        <StepModel
          providers={providers}
          credentials={credentials}
          keySource={keySource}
          onCredentials={setCredentials}
          onDone={() => goTo(1)}
        />
      ) : null}

      {step === 1 ? (
        <StepCalendar
          calendars={calendars}
          taskLists={taskLists}
          onBack={() => goTo(0)}
          onDone={() => goTo(2)}
        />
      ) : null}

      {step === 2 ? <StepReady onFinish={finish} /> : null}
    </main>
  );
}

// ---------------------------------------------------------------
function Stepper({ current }: { current: number }) {
  return (
    <ol
      className="mb-8 flex items-center gap-2"
      aria-label={`Passo ${current + 1} de ${STEPS.length}`}
    >
      {STEPS.map((label, index) => {
        const done = index < current;
        const active = index === current;
        return (
          <li key={label} className="flex flex-1 items-center gap-2.5">
            <span
              aria-current={active ? "step" : undefined}
              className={cx(
                "grid h-7 w-7 shrink-0 place-items-center rounded-full text-xs font-semibold",
                "transition-all duration-300",
                active
                  ? "bg-brand text-brand-ink shadow-brand"
                  : done
                    ? "bg-ok text-canvas"
                    : "bg-surface-2 text-muted ring-1 ring-inset ring-line-strong",
              )}
            >
              {done ? <IconCheck className="h-3.5 w-3.5" strokeWidth={3} /> : index + 1}
            </span>
            <span
              className={cx(
                "text-xs font-medium transition-colors",
                active ? "text-ink" : "text-muted",
              )}
            >
              {label}
            </span>
            {index < STEPS.length - 1 ? (
              <span
                aria-hidden
                className={cx(
                  "h-0.5 flex-1 rounded-full transition-colors duration-300",
                  done ? "bg-ok" : "bg-line",
                )}
              />
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

// ---------------------------------------------------------------
// Passo 1 — modelo
// ---------------------------------------------------------------
function StepModel({
  providers,
  credentials,
  keySource,
  onCredentials,
  onDone,
}: {
  providers: ProviderInfo[] | null;
  credentials: Credential[];
  keySource: KeySource | null;
  onCredentials: (next: Credential[]) => void;
  onDone: () => void;
}) {
  const visible = useMemo(
    () => (providers ?? []).filter((p) => !HIDDEN_IN_ONBOARDING.has(p.key)),
    [providers],
  );

  const [selected, setSelected] = useState(() => visible[0]?.key ?? "");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Credencial que já passou pela verificação. Quem já colou a chave
  // antes de sair não precisa colar de novo.
  const verified = credentials.find((c) => c.last_verified_at && !c.last_error);

  const provider = visible.find((p) => p.key === selected);
  const presentation = presentationFor(selected);
  const needsKey = presentation.keyUrl !== null || selected !== "ollama";

  // O operador forneceu uma chave (§3451), então este passo deixa de
  // ser obrigatório: a pessoa cria a conta e já conversa. É a diferença
  // entre um produto que alguém abre e usa e um que exige criar conta
  // no DeepSeek antes da primeira mensagem — o risco de produto que
  // §14 classificou como alto.
  const casaPaga = keySource?.source === "instance";

  async function saveAndVerify() {
    setError(null);
    setBusy(true);
    try {
      const created = await api.post<Credential>("/llm/credentials", {
        provider_key: selected,
        api_key: apiKey.trim(),
        default_model: model || provider?.default_model || undefined,
      });

      // Verificação imediata, antes de deixar avançar (§8.1).
      const result = await api.post<VerifyResult>(
        `/llm/credentials/${created.id}/verify`,
      );
      if (!result.ok) {
        setError(
          result.message ??
            "A chave não funcionou. Confira se copiou inteira e se a conta no provedor está ativa.",
        );
        setBusy(false);
        return;
      }

      // O `created` veio ANTES da verificação, então tem
      // `last_verified_at: null` — a rota de upsert zera o campo de
      // propósito ("chave nova precisa reverificar"). Guardar esse
      // objeto cru faria a tela, ao voltar um passo, achar que a chave
      // nunca foi verificada e pedir de novo.
      onCredentials([
        ...credentials.filter((c) => c.id !== created.id),
        { ...created, last_verified_at: new Date().toISOString(), last_error: null },
      ]);
      onDone();
    } catch (err) {
      setError(
        err instanceof RequestFailed
          ? err.message
          : "Não consegui salvar a chave. Tente de novo.",
      );
      setBusy(false);
    }
  }

  if (providers === null) {
    return (
      <Panel className="p-6 shadow-raised">
        <h1 className="text-xl font-semibold">Escolha de onde vem a inteligência</h1>
        <p
          className="mt-4 rounded-xl bg-danger/10 px-3.5 py-3 text-sm leading-relaxed text-danger"
          role="alert"
        >
          Não consegui carregar a lista de provedores — o serviço de IA pode estar
          subindo ainda.
        </p>
        <Button variant="secondary" className="mt-4" onClick={() => location.reload()}>
          Tentar de novo
        </Button>
      </Panel>
    );
  }

  return (
    <Panel className="p-6 shadow-raised">
      <h1 className="text-xl font-semibold">
        {casaPaga ? "Você já pode começar" : "Escolha de onde vem a inteligência"}
      </h1>
      <p className="mt-2.5 text-sm leading-relaxed text-muted">
        {casaPaga
          ? "O uso já está incluído, não precisa configurar nada para conversar. Se preferir usar sua própria chave de API, pode colar abaixo e aí não há limite de uso."
          : "O Age usa sua própria chave de API. Você paga só pelo que usar, direto no provedor. Nós não cobramos nada e não vemos suas conversas."}
      </p>

      {/* O caminho sem atrito, em primeiro lugar na tela: quem só quer
          usar o produto clica aqui e acabou. A opção de chave própria
          continua logo abaixo, para quem a quer — mas deixa de ser a
          única porta. */}
      {casaPaga && !verified ? (
        <div className="mt-5 rounded-xl bg-brand/[0.07] px-4 py-4 ring-1 ring-inset ring-brand/20">
          <p className="text-sm leading-relaxed">
            <strong>Uso incluído</strong>
            {keySource?.limit_usd
              ? ` — até US$ ${keySource.limit_usd.toFixed(2)} por mês, por conta da casa.`
              : ", por conta da casa."}
          </p>
          <Button variant="primary" className="mt-3.5" onClick={onDone}>
            Começar a usar
          </Button>
        </div>
      ) : null}

      {verified ? (
        <div className="mt-5 flex flex-wrap items-center gap-3 rounded-xl bg-ok/10 px-4 py-3.5 ring-1 ring-inset ring-ok/25">
          <span
            aria-hidden
            className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-ok text-canvas"
          >
            <IconCheck className="h-4 w-4" strokeWidth={3} />
          </span>
          <p className="min-w-0 flex-1 text-sm">
            Você já configurou <strong>{verified.provider_key}</strong>.
          </p>
          <Button variant="primary" size="sm" onClick={onDone}>
            Continuar
          </Button>
        </div>
      ) : null}

      {/* Quando a casa paga, o formulário de chave continua inteiro
          logo abaixo, mas precedido de um rótulo que o reposiciona como
          alternativa — e não como o pedágio da tela. A seção não é
          escondida num `<details>` de propósito: quem veio justamente
          para colar a própria chave não deveria ter de descobrir um
          clique a mais. */}
      {casaPaga ? (
        <p className="mt-6 border-t border-line pt-5 text-sm font-medium">
          Ou use sua própria chave
          <span className="ml-2 font-normal text-muted">
            sem limite de uso, pago direto no provedor
          </span>
        </p>
      ) : null}

      <fieldset className={cx("space-y-2", casaPaga ? "mt-3.5" : "mt-5")}>
        <legend className="sr-only">Provedor de LLM</legend>
        {visible.map((p) => {
          const info = presentationFor(p.key);
          const checked = p.key === selected;
          return (
            <label
              key={p.key}
              className={cx(
                "flex cursor-pointer gap-3 rounded-xl border px-4 py-3.5 transition-all duration-150",
                checked
                  ? "border-brand bg-brand/[0.07] ring-[3px] ring-brand/15"
                  : "border-line bg-surface-2 hover:border-line-strong hover:bg-surface-3",
              )}
            >
              <input
                type="radio"
                name="provider"
                value={p.key}
                checked={checked}
                onChange={() => {
                  setSelected(p.key);
                  setModel("");
                  setError(null);
                }}
                className="mt-0.5 h-4 w-4 shrink-0 cursor-pointer accent-brand"
              />
              <span className="flex-1">
                <span className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold">{p.name}</span>
                  {info.highlight ? (
                    <Badge tone={info.highlight === "free_tier" ? "ok" : "brand"}>
                      {HIGHLIGHT_LABEL[info.highlight]}
                    </Badge>
                  ) : null}
                  {info.costPerConversation ? (
                    <span className="tnum text-xs text-muted">
                      ~{brl(info.costPerConversation)} por conversa
                    </span>
                  ) : null}
                </span>
                {info.note ? (
                  <span className="mt-1 block text-xs leading-relaxed text-muted">
                    {info.note}
                  </span>
                ) : null}
              </span>
            </label>
          );
        })}
      </fieldset>

      {provider ? (
        <div className="mt-5 space-y-4">
          {presentation.keyUrl ? (
            <a
              href={presentation.keyUrl}
              target="_blank"
              rel="noreferrer noopener"
              className="inline-flex items-center gap-1.5 rounded-lg bg-surface-2 px-3 py-2 text-sm font-medium text-brand transition-colors hover:bg-surface-3"
            >
              <IconKey className="h-4 w-4" />
              Criar chave no {provider.name}
            </a>
          ) : null}

          {needsKey ? (
            <Field
              label="Cole a chave aqui"
              htmlFor="api-key"
              error={error}
              hint="A chave é cifrada antes de ir para o banco, e nunca volta numa resposta da API."
            >
              <Input
                id="api-key"
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="sk-…"
              />
            </Field>
          ) : null}

          {provider.models.length > 1 ? (
            <Field label="Modelo" htmlFor="model">
              <Select
                id="model"
                value={model || provider.default_model}
                onChange={(e) => setModel(e.target.value)}
              >
                {provider.models.map((m) => (
                  <option key={m} value={m}>
                    {m}
                    {m === provider.default_model ? " (recomendado)" : ""}
                  </option>
                ))}
              </Select>
            </Field>
          ) : null}

          <Button
            variant="primary"
            size="lg"
            full
            loading={busy}
            disabled={needsKey && apiKey.trim().length < 8}
            onClick={saveAndVerify}
          >
            {busy ? "Verificando com o provedor…" : "Verificar e continuar"}
          </Button>
        </div>
      ) : null}
    </Panel>
  );
}

// ---------------------------------------------------------------
// Passo 2 — agenda (saltável)
// ---------------------------------------------------------------
function StepCalendar({
  calendars,
  taskLists,
  onBack,
  onDone,
}: {
  calendars: CalendarRef[];
  taskLists: TaskListRef[];
  onBack: () => void;
  onDone: () => void;
}) {
  return (
    <Panel className="p-6 shadow-raised">
      <h1 className="text-xl font-semibold">Onde o Age vai escrever</h1>
      <p className="mt-2.5 text-sm leading-relaxed text-muted">
        Já existe uma agenda e uma lista aqui dentro, prontas para usar. Dá para
        conectar o Google depois, sem perder nada do que você criar agora.
      </p>

      <ul className="mt-5 space-y-2">
        {calendars.map((c) => (
          <li
            key={c.id}
            className="flex items-center gap-3 rounded-xl bg-surface-2 px-4 py-3"
          >
            <span
              aria-hidden
              className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-brand/12 text-brand"
            >
              <IconCalendar className="h-4 w-4" />
            </span>
            <span className="min-w-0 flex-1 text-sm font-medium">
              {c.name}
              {c.is_primary ? (
                <span className="ml-2 text-xs font-normal text-muted">principal</span>
              ) : null}
            </span>
            <Badge tone="ok">conectada</Badge>
          </li>
        ))}
        {taskLists.map((l) => (
          <li
            key={l.id}
            className="flex items-center gap-3 rounded-xl bg-surface-2 px-4 py-3"
          >
            <span
              aria-hidden
              className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-brand/12 text-brand"
            >
              <IconTasks className="h-4 w-4" />
            </span>
            <span className="min-w-0 flex-1 text-sm font-medium">{l.name}</span>
            <Badge tone="ok">conectada</Badge>
          </li>
        ))}
      </ul>

      <div className="mt-4 rounded-xl border border-dashed border-line-strong px-4 py-3.5">
        <p className="text-sm font-medium text-ink-2">Google Calendar</p>
        <p className="mt-1 text-xs leading-relaxed text-muted">
          Ainda não disponível nesta versão. Quando estiver, aparece em Configurações →
          Conexões, e o que você já tiver criado continua onde está.
        </p>
      </div>

      <div className="mt-6 flex flex-wrap items-center gap-2">
        <Button variant="primary" size="lg" onClick={onDone}>
          Usar a agenda do Age
        </Button>
        {/* "Depois", não "pular": a decisão é reversível, e dizer
            "pular" sugere que a pessoa está perdendo algo (§8.1). */}
        <Button variant="ghost" onClick={onDone}>
          Decidir depois
        </Button>
        <Button variant="quiet" size="sm" className="ml-auto" onClick={onBack}>
          <IconChevronLeft className="h-3.5 w-3.5" />
          Voltar
        </Button>
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------
// Passo 3 — pronto
// ---------------------------------------------------------------
/**
 * Os exemplos são os mesmos do estado vazio do chat (§8.1), e isso é
 * de propósito: a pessoa acabou de ler estas frases aqui, então
 * reencontrá-las na tela seguinte confirma que entendeu o produto, em
 * vez de apresentar quatro ideias novas.
 */
const EXAMPLES = [
  "preciso estudar cálculo II, prova dia 20",
  "quero academia 3x por semana",
  "o que eu tenho pra fazer essa semana?",
  "minha semana que vem tá pesada?",
];

function StepReady({ onFinish }: { onFinish: () => void }) {
  return (
    <Panel className="p-6 shadow-raised">
      <h1 className="text-2xl font-semibold">Pronto.</h1>
      <p className="mt-2.5 text-sm leading-relaxed text-muted">
        O Age conversa — você escreve o que precisa organizar, ele propõe, e nada entra
        na sua agenda sem você aprovar.
      </p>

      <p className="eyebrow mt-6">Experimente dizer</p>
      <ul className="stagger mt-2 space-y-2">
        {EXAMPLES.map((example, i) => (
          <li
            key={example}
            style={{ ["--i" as string]: i }}
            className="rounded-xl bg-surface-2 px-4 py-3 text-sm text-ink-2"
          >
            &ldquo;{example}&rdquo;
          </li>
        ))}
      </ul>

      <Button variant="primary" size="lg" full className="mt-6" onClick={onFinish}>
        Começar
      </Button>
    </Panel>
  );
}
