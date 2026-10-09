/**
 * Sidebar de conversas (§8.1).
 *
 * "No modelo que ChatGPT e Gemini consolidaram — com uma diferença: o
 * Age mostra se a conversa **gerou algo na sua agenda**, porque é o que
 * torna o histórico útil aqui."
 *
 * O contador `8📅` / `6📋` é o que diferencia "conversa que resolveu
 * algo" de "conversa que não foi pra frente" ao olhar a lista meses
 * depois. Sem ele a lista é uma pilha de títulos, e o histórico perde a
 * função.
 */

"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { Badge, Button, Dialog, Input, Spinner } from "@/components/ui";
import { BUCKET_LABEL, recencyBucket, type RecencyBucket } from "@/lib/format";
import type { Conversation } from "@/lib/types";

export interface ConversationCounts {
  /** Itens de plano aplicados nesta conversa, por destino (§5.7). */
  events: number;
  tasks: number;
}

export function ConversationSidebar({
  conversations,
  counts,
  activeId,
  timezone,
  loading,
  onNew,
  onSearch,
  onRename,
  onPin,
  onArchive,
  onDelete,
  onFork,
}: {
  conversations: Conversation[];
  counts: Record<string, ConversationCounts>;
  activeId: string | null;
  timezone: string;
  loading?: boolean;
  onNew: () => void;
  onSearch: (term: string) => void;
  onRename: (id: string, title: string) => void;
  onPin: (id: string, pinned: boolean) => void;
  onArchive: (id: string) => void;
  onDelete: (id: string) => void;
  onFork: (id: string) => void;
}) {
  const [term, setTerm] = useState("");
  const [renaming, setRenaming] = useState<Conversation | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [showArchived, setShowArchived] = useState(false);

  const groups = useMemo(() => {
    const pinned: Conversation[] = [];
    const byBucket: Record<RecencyBucket, Conversation[]> = {
      hoje: [],
      "7dias": [],
      anteriores: [],
    };
    const archived: Conversation[] = [];

    for (const conversation of conversations) {
      if (conversation.archived_at) {
        archived.push(conversation);
        continue;
      }
      if (conversation.pinned_at) {
        pinned.push(conversation);
        continue;
      }
      const when = conversation.last_message_at ?? conversation.created_at;
      byBucket[recencyBucket(when, timezone)].push(conversation);
    }
    return { pinned, byBucket, archived };
  }, [conversations, timezone]);

  return (
    <aside className="flex h-full w-full flex-col border-r border-[var(--border)] bg-[var(--panel)]">
      <div className="space-y-2 border-b border-[var(--border)] p-3">
        <Button variant="primary" className="w-full" onClick={onNew}>
          + Nova conversa
        </Button>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            onSearch(term.trim());
          }}
        >
          <label htmlFor="conv-search" className="sr-only">
            Buscar nas conversas
          </label>
          <Input
            id="conv-search"
            type="search"
            placeholder="buscar…"
            value={term}
            onChange={(e) => {
              setTerm(e.target.value);
              if (e.target.value === "") onSearch("");
            }}
          />
        </form>
      </div>

      <nav aria-label="Conversas" className="flex-1 overflow-y-auto px-2 py-2">
        {loading && conversations.length === 0 ? (
          <p className="px-2 py-4 text-xs text-[var(--muted)]">
            <Spinner /> carregando…
          </p>
        ) : null}

        {groups.pinned.length > 0 ? (
          <Group label="📌 Fixadas">
            {groups.pinned.map((c) => (
              <Row
                key={c.id}
                conversation={c}
                counts={counts[c.id]}
                active={c.id === activeId}
                onRename={() => {
                  setRenaming(c);
                  setDraftTitle(c.title ?? "");
                }}
                onPin={() => onPin(c.id, false)}
                onArchive={() => onArchive(c.id)}
                onDelete={() => onDelete(c.id)}
                onFork={() => onFork(c.id)}
              />
            ))}
          </Group>
        ) : null}

        {(["hoje", "7dias", "anteriores"] as const).map((bucket) =>
          groups.byBucket[bucket].length > 0 ? (
            <Group key={bucket} label={BUCKET_LABEL[bucket]}>
              {groups.byBucket[bucket].map((c) => (
                <Row
                  key={c.id}
                  conversation={c}
                  counts={counts[c.id]}
                  active={c.id === activeId}
                  onRename={() => {
                    setRenaming(c);
                    setDraftTitle(c.title ?? "");
                  }}
                  onPin={() => onPin(c.id, true)}
                  onArchive={() => onArchive(c.id)}
                  onDelete={() => onDelete(c.id)}
                  onFork={() => onFork(c.id)}
                />
              ))}
            </Group>
          ) : null,
        )}

        {!loading && conversations.length === 0 ? (
          <p className="px-2 py-4 text-xs leading-relaxed text-[var(--muted)]">
            Nenhuma conversa ainda.
          </p>
        ) : null}
      </nav>

      {groups.archived.length > 0 ? (
        <div className="border-t border-[var(--border)] p-2">
          <button
            onClick={() => setShowArchived((v) => !v)}
            aria-expanded={showArchived}
            className="w-full rounded-md px-2 py-1.5 text-left text-xs text-[var(--muted)] hover:bg-[var(--panel-2)]"
          >
            🗄 Arquivadas ({groups.archived.length})
          </button>
          {showArchived ? (
            <ul className="mt-1 space-y-0.5">
              {groups.archived.map((c) => (
                <li key={c.id}>
                  <Link
                    href={`/chat/${c.id}`}
                    className="block truncate rounded-md px-2 py-1.5 text-xs text-[var(--muted)] hover:bg-[var(--panel-2)]"
                  >
                    {c.title ?? "Sem título"}
                  </Link>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      <Dialog
        open={renaming !== null}
        onClose={() => setRenaming(null)}
        title="Renomear conversa"
        footer={
          <>
            <Button variant="quiet" onClick={() => setRenaming(null)}>
              Cancelar
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                if (renaming) onRename(renaming.id, draftTitle.trim());
                setRenaming(null);
              }}
              disabled={draftTitle.trim().length === 0}
            >
              Salvar
            </Button>
          </>
        }
      >
        <label htmlFor="rename" className="sr-only">
          Novo título
        </label>
        <Input
          id="rename"
          value={draftTitle}
          onChange={(e) => setDraftTitle(e.target.value)}
          maxLength={200}
          autoFocus
        />
      </Dialog>
    </aside>
  );
}

function Group({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-3">
      <h3 className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-wide text-[var(--muted)]">
        {label}
      </h3>
      <ul className="space-y-0.5">{children}</ul>
    </div>
  );
}

function Row({
  conversation,
  counts,
  active,
  onRename,
  onPin,
  onArchive,
  onDelete,
  onFork,
}: {
  conversation: Conversation;
  counts?: ConversationCounts;
  active: boolean;
  onRename: () => void;
  onPin: () => void;
  onArchive: () => void;
  onDelete: () => void;
  onFork: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const title = conversation.title ?? "Sem título";

  return (
    <li className="group relative">
      <Link
        href={`/chat/${conversation.id}`}
        aria-current={active ? "page" : undefined}
        className={[
          "flex items-center gap-2 rounded-md px-2 py-2 text-sm transition-colors",
          active
            ? "bg-[var(--panel-2)] text-[var(--text)]"
            : "text-[var(--muted)] hover:bg-[var(--panel-2)] hover:text-[var(--text)]",
        ].join(" ")}
      >
        <span className="min-w-0 flex-1 truncate">{title}</span>

        {/* O contador de itens aplicados: a razão desta sidebar ser
            diferente das outras (§8.1). */}
        {counts && counts.events > 0 ? (
          <span
            className="shrink-0 text-[10px] text-[var(--accent)]"
            title={`${counts.events} na agenda`}
          >
            {counts.events}📅
          </span>
        ) : null}
        {counts && counts.tasks > 0 ? (
          <span
            className="shrink-0 text-[10px] text-[var(--accent)]"
            title={`${counts.tasks} na lista`}
          >
            {counts.tasks}📋
          </span>
        ) : null}
      </Link>

      <button
        onClick={() => setMenuOpen((v) => !v)}
        aria-label={`Ações de ${title}`}
        aria-expanded={menuOpen}
        className="absolute right-1 top-1.5 rounded px-1.5 py-0.5 text-xs text-[var(--muted)] opacity-0 hover:bg-[var(--bg)] focus-visible:opacity-100 group-hover:opacity-100"
      >
        ⋯
      </button>

      {menuOpen ? (
        <ul
          role="menu"
          className="absolute right-1 top-8 z-20 w-40 overflow-hidden rounded-md border border-[var(--border-strong)] bg-[var(--panel-2)] py-1 text-xs shadow-xl"
        >
          {[
            { label: "Renomear", run: onRename },
            {
              label: conversation.pinned_at ? "Desafixar" : "Fixar",
              run: onPin,
            },
            // §8.1: ramificar preserva as duas versões em vez de
            // sobrescrever, que é o que editar mensagem antiga faz nos
            // outros produtos.
            { label: "Ramificar", run: onFork },
            { label: "Arquivar", run: onArchive },
          ].map((action) => (
            <li key={action.label}>
              <button
                role="menuitem"
                onClick={() => {
                  setMenuOpen(false);
                  action.run();
                }}
                className="block w-full px-3 py-1.5 text-left hover:bg-[var(--bg)]"
              >
                {action.label}
              </button>
            </li>
          ))}
          <li>
            <button
              role="menuitem"
              onClick={() => {
                setMenuOpen(false);
                onDelete();
              }}
              className="block w-full px-3 py-1.5 text-left text-[var(--danger)] hover:bg-[var(--bg)]"
            >
              Apagar
            </button>
          </li>
        </ul>
      ) : null}
    </li>
  );
}
