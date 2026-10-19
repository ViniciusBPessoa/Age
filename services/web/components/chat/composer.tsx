/**
 * O campo de mensagem e os dois controles do rodapé (§8.1).
 *
 *   ┌──────────────────────────────────────────────────────────┐
 *   │  preciso revisar o capítulo 2 até sexta                  │
 *   │  DeepSeek ▾    Ambos ▾                            [ ↑ ]  │
 *   └──────────────────────────────────────────────────────────┘
 *
 * O seletor de destino é a camada 2 do seletor de três camadas de
 * §5.7.2: conta → **conversa** → mensagem. Mudar aqui define o padrão
 * da conversa, e uma mensagem pode sobrepor ("só põe na lista") — e
 * quando sobrepõe, o chip pisca no valor usado, para a pessoa ver que
 * foi entendida sem precisar conferir o diff.
 */

"use client";

import { useEffect, useRef, useState } from "react";

import { Badge, Button, IconSend, Select, cx } from "@/components/ui";
import type { Credential, ProviderInfo, Target } from "@/lib/types";

const TARGET_LABEL: Record<Target, string> = {
  event: "Agenda",
  task: "Lista",
  both: "Ambos",
  ask: "Perguntar",
};

const TARGET_HINT: Record<Target, string> = {
  event: "Vai para a agenda, com hora marcada.",
  task: "Vai para a lista, sem ocupar horário.",
  both: "Tarefa na lista e bloco de tempo na agenda.",
  ask: "O assistente pergunta antes de decidir.",
};

export function Composer({
  providers,
  credentials,
  providerKey,
  model,
  target,
  /** Destino que a ÚLTIMA mensagem usou, se sobrepôs o da conversa. */
  overriddenTarget,
  disabled,
  sending,
  onSend,
  onProviderChange,
  onTargetChange,
}: {
  providers: ProviderInfo[];
  credentials: Credential[];
  providerKey: string | null;
  model: string | null;
  target: Target;
  overriddenTarget?: Target | null;
  disabled?: boolean;
  sending?: boolean;
  onSend: (text: string) => void;
  onProviderChange: (providerKey: string, model: string) => void;
  onTargetChange: (target: Target) => void;
}) {
  const [text, setText] = useState("");
  const area = useRef<HTMLTextAreaElement>(null);
  const [pulse, setPulse] = useState(false);

  // O chip pisca quando a mensagem sobrepôs o padrão (§8.1).
  useEffect(() => {
    if (!overriddenTarget) return;
    setPulse(true);
    const timer = setTimeout(() => setPulse(false), 2600);
    return () => clearTimeout(timer);
  }, [overriddenTarget]);

  // Cresce com o conteúdo, até um teto. Um campo de uma linha obriga a
  // rolar dentro dele para reler o que se escreveu.
  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [text]);

  // Só credenciais que funcionam entram no seletor. Oferecer uma chave
  // que falhou na última verificação é empurrar a pessoa para um erro
  // que já é conhecido.
  const usable = providers.filter((p) =>
    credentials.some((c) => c.provider_key === p.key && c.is_active),
  );
  const broken = credentials.filter((c) => c.last_error);
  const canSend = text.trim().length > 0 && !disabled && !sending;

  function submit() {
    if (!canSend) return;
    onSend(text.trim());
    setText("");
  }

  return (
    <div className="px-4 pb-4 pt-2 md:px-6">
      <div className="mx-auto max-w-3xl">
        {broken.length > 0 ? (
          <p
            role="status"
            className="mb-2 flex items-center gap-2 rounded-lg bg-warn/10 px-3 py-2 text-xs text-warn ring-1 ring-inset ring-warn/20"
          >
            A chave do {broken[0].provider_key} falhou na última verificação. Dá para
            corrigir em Configurações.
          </p>
        ) : null}

        {/*
         * A caixa inteira é a superfície de foco, com anel.
         *
         * `focus-within` no contêiner em vez de borda no textarea: o
         * campo e os dois seletores vivem dentro da mesma moldura, e o
         * anel precisa envolver o conjunto, não só a linha de texto.
         */}
        <div
          className={cx(
            "rounded-2xl border border-line-strong bg-surface shadow-raised",
            "transition-all duration-200",
            "focus-within:border-brand focus-within:ring-[3px] focus-within:ring-brand/20",
          )}
        >
          <label htmlFor="composer" className="sr-only">
            Mensagem
          </label>
          <textarea
            id="composer"
            ref={area}
            rows={1}
            value={text}
            disabled={disabled}
            placeholder="Peça algo, ou conte o que precisa organizar…"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              // Enter envia, Shift+Enter quebra linha — a convenção que
              // todo chat usa, e contrariá-la custa uma mensagem
              // enviada por engano.
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            className="w-full resize-none bg-transparent px-4 pt-3.5 text-base leading-relaxed text-ink outline-none placeholder:text-faint disabled:opacity-50"
          />

          <div className="flex flex-wrap items-center gap-1.5 px-2.5 pb-2.5 pt-1">
            {/* Seletor de provedor/modelo (§3.1.3: trocar afeta a
                próxima mensagem, a conversa segue com o mesmo
                histórico). */}
            <label htmlFor="provider-select" className="sr-only">
              Provedor e modelo
            </label>
            <Select
              bare
              id="provider-select"
              value={`${providerKey ?? ""}::${model ?? ""}`}
              disabled={usable.length === 0}
              onChange={(e) => {
                const [key, chosen] = e.target.value.split("::");
                onProviderChange(key, chosen);
              }}
            >
              {usable.length === 0 ? <option value="::">sem credencial</option> : null}
              {usable.map((provider) =>
                provider.models.map((candidate) => (
                  <option
                    key={`${provider.key}::${candidate}`}
                    value={`${provider.key}::${candidate}`}
                  >
                    {provider.name} · {candidate}
                  </option>
                )),
              )}
            </Select>

            <span aria-hidden className="h-4 w-px bg-line" />

            {/* Seletor de destino (§5.7.2). */}
            <label htmlFor="target-select" className="sr-only">
              Para onde vai o que for proposto
            </label>
            <Select
              bare
              id="target-select"
              value={target}
              title={TARGET_HINT[target]}
              onChange={(e) => onTargetChange(e.target.value as Target)}
              className={cx(pulse && "age-pulse rounded-lg")}
            >
              {(Object.keys(TARGET_LABEL) as Target[]).map((value) => (
                <option key={value} value={value}>
                  {TARGET_LABEL[value]}
                </option>
              ))}
            </Select>

            {/* Quando a mensagem sobrepôs, dizer qual valor valeu. O
                `aria-live` faz o leitor de tela anunciar — piscar
                sozinho não comunica nada para quem não vê. */}
            {overriddenTarget && overriddenTarget !== target ? (
              <span aria-live="polite">
                <Badge tone="brand">
                  esta foi para {TARGET_LABEL[overriddenTarget].toLowerCase()}
                </Badge>
              </span>
            ) : null}

            <Button
              variant="primary"
              size="icon"
              className="ml-auto rounded-xl"
              onClick={submit}
              loading={sending}
              disabled={!canSend}
              aria-label="Enviar mensagem"
            >
              {sending ? null : <IconSend />}
            </Button>
          </div>
        </div>

        {/*
         * A dica inteira so no desktop.
         *
         * Em 390px ela quebra em duas linhas e encosta na barra de
         * navegacao de baixo, que e fixa: a segunda linha sai cortada.
         * No celular fica a parte que a pessoa precisa saber (nada
         * entra sem aprovar); "Enter envia" nao vale a linha extra em
         * quem digita com teclado virtual.
         */}
        <p className="mt-2 text-center text-2xs text-faint">
          <span className="hidden sm:inline">
            Enter envia · Shift+Enter quebra linha ·{" "}
          </span>
          nada é escrito na sua agenda sem você aprovar
        </p>
      </div>
    </div>
  );
}
