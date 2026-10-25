/**
 * Sidebar de conversas (§8.1).
 *
 * "No modelo que ChatGPT e Gemini consolidaram — com uma diferença: o
 * Age mostra se a conversa **gerou algo na sua agenda**, porque é o que
 * torna o histórico útil aqui."
 *
 * O contador de itens aplicados é o que diferencia "conversa que
 * resolveu algo" de "conversa que não foi pra frente" ao olhar a lista
 * meses depois. Sem ele a lista é uma pilha de títulos, e o histórico
 * perde a função.
 *
 * Os contadores eram `8📅` / `6📋`. Emoji colado em número fica
 * ilegível em 10px e muda de desenho por sistema; agora são ícone de
 * traço mais número, no mesmo tom do acento.
 */

"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  Button,
  Dialog,
  IconArchive,
  IconCalendar,
  IconPin,
  IconPlus,
  IconSearch,
  IconTasks,
  Input,
  SkeletonList,
  cx,
} from "@/components/ui";
import { BUCKET_LABEL, recencyBucket, type RecencyBucket } from "@/lib/format";
import type { Conversation } from "@/lib/types";
import { EVENTO_RASCUNHO, conversasComRascunho } from "@/lib/rascunho";

export interface ConversationCounts {
  /** Itens de plano aplicados nesta conversa, por destino (§5.7). */
  events: number;
  tasks: number;
}

/**
 * As conversas com texto não enviado.
 *
 * Lê as chaves de uma vez, não uma por linha: a lista pode ter dezenas
 * de conversas, e `localStorage` é síncrono — uma leitura por item
 * bloquearia a rolagem.
 *
 * Reage ao evento próprio porque `storage` do navegador só dispara em
 * OUTRAS abas: digitar nesta aba não avisaria a lista.
 */
function useConversasComRascunho(): Set<string> {
  const [ids, setIds] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    const atualizar = () => setIds(conversasComRascunho());
    atualizar();
    window.addEventListener(EVENTO_RASCUNHO, atualizar);
    // `storage` cobre o caso de outra aba apagar ou escrever.
    window.addEventListener("storage", atualizar);
    return () => {
      window.removeEventListener(EVENTO_RASCUNHO, atualizar);
      window.removeEventListener("storage", atualizar);
    };
  }, []);

  return ids;
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
  onDeleteMany,
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
  /** Apaga várias de uma vez (§6.2.4). Ausente = modo seleção some. */
  onDeleteMany?: (ids: string[]) => Promise<void> | void;
  onFork: (id: string) => void;
}) {
  const [term, setTerm] = useState("");
  const [renaming, setRenaming] = useState<Conversation | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [showArchived, setShowArchived] = useState(false);

  /*
   * Modo seleção (§6.2.4).
   *
   * Apagar uma por uma pelo menu de três pontos já existia, e com 20
   * conversas acumuladas isso são 60 cliques. O pedido foi "apagar
   * todos, ou um lugar onde eu escolha quais".
   *
   * Fica atrás de um modo em vez de caixas sempre visíveis: a lista é
   * para navegar, e caixa de seleção em cada linha cobra atenção de
   * quem só quer trocar de conversa.
   */
  const [selecting, setSelecting] = useState(false);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [confirming, setConfirming] = useState(false);
  const [apagandoUma, setApagandoUma] = useState<Conversation | null>(null);
  const [erasing, setErasing] = useState(false);

  // Só o que o usuário VÊ pode ser selecionado: aplicar "selecionar
  // tudo" a conversas escondidas por um filtro de busca apagaria coisa
  // que ele não tinha na tela.
  const visiveis = useMemo(
    () => conversations.filter((c) => !c.archived_at),
    [conversations],
  );
  const todasMarcadas = visiveis.length > 0 && chosen.size === visiveis.length;

  function sairDaSelecao() {
    setSelecting(false);
    setChosen(new Set());
  }

  function alternar(id: string) {
    setChosen((atual) => {
      const proximo = new Set(atual);
      if (proximo.has(id)) proximo.delete(id);
      else proximo.add(id);
      return proximo;
    });
  }

  async function apagarEscolhidas() {
    if (!onDeleteMany || chosen.size === 0) return;
    setErasing(true);
    try {
      await onDeleteMany([...chosen]);
      setConfirming(false);
      sairDaSelecao();
    } finally {
      setErasing(false);
    }
  }
  // Um menu aberto por vez, controlado aqui: antes cada linha guardava o
  // seu próprio `menuOpen`, então abrir o segundo deixava o primeiro
  // aberto atrás dele.
  const [openMenu, setOpenMenu] = useState<string | null>(null);

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

  const comRascunho = useConversasComRascunho();

  const rowProps = (c: Conversation) => ({
    conversation: c,
    counts: counts[c.id],
    temRascunho: comRascunho.has(c.id),
    active: c.id === activeId,
    selecting,
    checked: chosen.has(c.id),
    onToggle: () => alternar(c.id),
    menuOpen: openMenu === c.id,
    onMenu: (open: boolean) => setOpenMenu(open ? c.id : null),
    onRename: () => {
      setRenaming(c);
      setDraftTitle(c.title ?? "");
    },
    onPin: () => onPin(c.id, !c.pinned_at),
    onArchive: () => onArchive(c.id),
    // Pede confirmação em vez de apagar direto. Apagar pelo menu de
    // três pontos era a única ação destrutiva da tela sem pergunta, e
    // o item fica a um pixel de "Arquivar", que é reversível.
    onDelete: () => setApagandoUma(c),
    onFork: () => onFork(c.id),
  });

  return (
    <aside className="flex h-full w-full flex-col border-r border-line bg-canvas">
      <div className="space-y-2.5 p-3">
        <Button variant="primary" full onClick={onNew}>
          <IconPlus />
          Nova conversa
        </Button>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            onSearch(term.trim());
          }}
          className="relative"
        >
          <label htmlFor="conv-search" className="sr-only">
            Buscar nas conversas
          </label>
          <IconSearch className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-faint" />
          <Input
            id="conv-search"
            type="search"
            placeholder="Buscar"
            value={term}
            onChange={(e) => {
              setTerm(e.target.value);
              if (e.target.value === "") onSearch("");
            }}
            className="h-9 pl-9 text-xs"
          />
        </form>

        {onDeleteMany && visiveis.length > 0 ? (
          selecting ? (
            <div className="space-y-2 rounded-lg border border-line bg-surface-2 p-2">
              <div className="flex items-center justify-between gap-2">
                <label className="flex cursor-pointer items-center gap-2 text-xs text-ink-2">
                  <input
                    type="checkbox"
                    checked={todasMarcadas}
                    onChange={() =>
                      setChosen(
                        todasMarcadas ? new Set() : new Set(visiveis.map((c) => c.id)),
                      )
                    }
                    className="h-3.5 w-3.5 accent-brand"
                  />
                  Selecionar todas
                </label>
                <span className="tabular-nums text-2xs text-muted">
                  {chosen.size} de {visiveis.length}
                </span>
              </div>
              <div className="flex gap-1.5">
                <Button
                  variant="ghost"
                  className="h-7 flex-1 text-xs"
                  onClick={sairDaSelecao}
                >
                  Cancelar
                </Button>
                <Button
                  variant="danger"
                  className="h-7 flex-1 text-xs"
                  disabled={chosen.size === 0}
                  onClick={() => setConfirming(true)}
                >
                  Apagar {chosen.size > 0 ? chosen.size : ""}
                </Button>
              </div>
            </div>
          ) : (
            <button
              onClick={() => setSelecting(true)}
              className="w-full rounded-lg px-2.5 py-1.5 text-left text-xs text-muted transition-colors hover:bg-surface-2 hover:text-ink"
            >
              Selecionar conversas
            </button>
          )
        ) : null}
      </div>

      <nav aria-label="Conversas" className="flex-1 overflow-y-auto px-2 pb-2">
        {loading && conversations.length === 0 ? (
          <div className="space-y-1.5 px-1 py-2" aria-hidden>
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="skeleton h-8" />
            ))}
          </div>
        ) : null}

        {groups.pinned.length > 0 ? (
          <Group label="Fixadas" icon={<IconPin className="h-3 w-3" />}>
            {groups.pinned.map((c) => (
              <Row key={c.id} {...rowProps(c)} />
            ))}
          </Group>
        ) : null}

        {(["hoje", "7dias", "anteriores"] as const).map((bucket) =>
          groups.byBucket[bucket].length > 0 ? (
            <Group key={bucket} label={BUCKET_LABEL[bucket]}>
              {groups.byBucket[bucket].map((c) => (
                <Row key={c.id} {...rowProps(c)} />
              ))}
            </Group>
          ) : null,
        )}

        {!loading && conversations.length === 0 ? (
          <p className="px-3 py-6 text-center text-xs leading-relaxed text-muted">
            Nenhuma conversa ainda.
            <br />
            Comece pedindo algo ao Age.
          </p>
        ) : null}
      </nav>

      {groups.archived.length > 0 ? (
        <div className="border-t border-line p-2">
          <button
            onClick={() => setShowArchived((v) => !v)}
            aria-expanded={showArchived}
            className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs text-muted transition-colors hover:bg-surface-2 hover:text-ink"
          >
            <IconArchive className="h-3.5 w-3.5" />
            Arquivadas
            <span className="ml-auto tabular-nums text-faint">
              {groups.archived.length}
            </span>
          </button>
          {showArchived ? (
            <ul className="mt-1 space-y-0.5">
              {groups.archived.map((c) => (
                <li key={c.id}>
                  <Link
                    href={`/chat/${c.id}`}
                    className="block truncate rounded-lg px-2.5 py-1.5 text-xs text-muted transition-colors hover:bg-surface-2 hover:text-ink"
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
        open={apagandoUma !== null}
        onClose={() => setApagandoUma(null)}
        title="Apagar esta conversa?"
        description={apagandoUma?.title ?? "Sem título"}
        footer={
          <>
            <Button variant="ghost" onClick={() => setApagandoUma(null)}>
              Cancelar
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                if (apagandoUma) onDelete(apagandoUma.id);
                setApagandoUma(null);
              }}
            >
              Apagar
            </Button>
          </>
        }
      >
        <p className="text-sm leading-relaxed text-ink-2">
          O que você já aprovou continua na agenda e nas tarefas. Some só o histórico da
          conversa, e dá para recuperar por 30 dias.
        </p>
      </Dialog>

      {/* Confirmação explícita: apagar em lote é a ação mais destrutiva
          desta tela, e o diálogo diz QUANTAS e o que NÃO é perdido —
          §6.2.3, eventos e tarefas já aprovados continuam na agenda. */}
      <Dialog
        open={confirming}
        onClose={() => (erasing ? undefined : setConfirming(false))}
        title={
          chosen.size === visiveis.length && visiveis.length > 1
            ? "Apagar todas as conversas?"
            : `Apagar ${chosen.size} ${chosen.size === 1 ? "conversa" : "conversas"}?`
        }
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => setConfirming(false)}
              disabled={erasing}
            >
              Cancelar
            </Button>
            <Button
              variant="danger"
              onClick={() => void apagarEscolhidas()}
              disabled={erasing}
            >
              {erasing ? "Apagando…" : "Apagar"}
            </Button>
          </>
        }
      >
        <p className="text-sm leading-relaxed text-ink-2">
          O que você já aprovou continua na agenda e nas tarefas. Some só o histórico da
          conversa, e dá para recuperar por 30 dias.
        </p>
      </Dialog>

      <Dialog
        open={renaming !== null}
        onClose={() => setRenaming(null)}
        title="Renomear conversa"
        footer={
          <>
            <Button variant="ghost" onClick={() => setRenaming(null)}>
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

function Group({
  label,
  icon,
  children,
}: {
  label: string;
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="mb-4">
      <h3 className="eyebrow flex items-center gap-1.5 px-2.5 pb-1.5">
        {icon}
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
  selecting,
  checked,
  onToggle,
  menuOpen,
  onMenu,
  onRename,
  onPin,
  onArchive,
  onDelete,
  onFork,
  temRascunho,
}: {
  conversation: Conversation;
  counts?: ConversationCounts;
  active: boolean;
  selecting: boolean;
  checked: boolean;
  onToggle: () => void;
  menuOpen: boolean;
  onMenu: (open: boolean) => void;
  onRename: () => void;
  onPin: () => void;
  onArchive: () => void;
  onDelete: () => void;
  onFork: () => void;
  /** Tem texto escrito e não enviado nesta conversa. */
  temRascunho?: boolean;
}) {
  const title = conversation.title ?? "Sem título";
  const wrap = useRef<HTMLLIElement>(null);

  /*
   * Fechar ao clicar fora e no Escape.
   *
   * Faltava, e a falta era sentida: o menu ficava aberto até alguém
   * acertar o mesmo botão de novo, inclusive depois de navegar para
   * outra conversa. `mousedown` em vez de `click` para fechar antes que
   * o clique vire navegação em outro item.
   */
  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!wrap.current?.contains(e.target as Node)) onMenu(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onMenu(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen, onMenu]);

  const actions = [
    { label: "Renomear", run: onRename },
    { label: conversation.pinned_at ? "Desafixar" : "Fixar", run: onPin },
    // §8.1: ramificar preserva as duas versões em vez de sobrescrever,
    // que é o que editar mensagem antiga faz nos outros produtos.
    { label: "Ramificar", run: onFork },
    { label: "Arquivar", run: onArchive },
  ];

  /*
   * No modo seleção a linha deixa de ser link e vira rótulo da caixa.
   *
   * Se continuasse navegando, clicar para marcar trocaria de conversa e
   * tiraria o usuário da lista no meio da escolha — o clique faria duas
   * coisas, e a errada seria a que ele mais nota.
   */
  if (selecting) {
    return (
      <li>
        <label
          className={cx(
            "flex cursor-pointer items-center gap-2.5 rounded-lg py-2 pl-2.5 pr-2 text-sm transition-colors",
            checked ? "bg-surface-2 text-ink" : "text-muted hover:bg-surface-2/60",
          )}
        >
          <input
            type="checkbox"
            checked={checked}
            onChange={onToggle}
            className="h-3.5 w-3.5 shrink-0 accent-brand"
          />
          <span className="min-w-0 flex-1 truncate">{title}</span>
        </label>
      </li>
    );
  }

  return (
    <li ref={wrap} className="group relative">
      <Link
        href={`/chat/${conversation.id}`}
        aria-current={active ? "page" : undefined}
        className={cx(
          "flex items-center gap-2 rounded-lg py-2 pl-2.5 pr-8 text-sm transition-colors duration-150",
          active
            ? "bg-surface-2 font-medium text-ink"
            : "text-muted hover:bg-surface-2/60 hover:text-ink",
        )}
      >
        <span className="min-w-0 flex-1 truncate">{title}</span>

        {/*
         * Mensagem escrita e não enviada.
         *
         * `title` e `aria-label` não são enfeite: um ponto colorido
         * sozinho não existe para quem não enxerga, nem para quem não
         * adivinha a convenção. §8.2.
         */}
        {temRascunho ? (
          <span
            title="Mensagem não enviada"
            aria-label="Mensagem não enviada"
            role="img"
            className="h-1.5 w-1.5 shrink-0 rounded-full bg-brand"
          />
        ) : null}

        {/* O contador de itens aplicados: a razão desta sidebar ser
            diferente das outras (§8.1). */}
        {counts && counts.events > 0 ? (
          <span
            className="flex shrink-0 items-center gap-0.5 text-2xs font-medium text-brand"
            title={`${counts.events} na agenda`}
          >
            <IconCalendar className="h-3 w-3" />
            {counts.events}
          </span>
        ) : null}
        {counts && counts.tasks > 0 ? (
          <span
            className="flex shrink-0 items-center gap-0.5 text-2xs font-medium text-brand"
            title={`${counts.tasks} na lista`}
          >
            <IconTasks className="h-3 w-3" />
            {counts.tasks}
          </span>
        ) : null}
      </Link>

      <button
        onClick={() => onMenu(!menuOpen)}
        aria-label={`Ações de ${title}`}
        aria-expanded={menuOpen}
        className={cx(
          "absolute right-1 top-1/2 grid h-6 w-6 -translate-y-1/2 place-items-center rounded-md",
          "text-muted transition-all hover:bg-surface-3 hover:text-ink",
          "focus-visible:opacity-100 group-hover:opacity-100",
          menuOpen ? "bg-surface-3 text-ink opacity-100" : "opacity-0",
        )}
      >
        <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden className="h-4 w-4">
          <circle cx="12" cy="5.5" r="1.6" />
          <circle cx="12" cy="12" r="1.6" />
          <circle cx="12" cy="18.5" r="1.6" />
        </svg>
      </button>

      {menuOpen ? (
        <ul
          role="menu"
          className="animate-scale-in absolute right-1 top-9 z-30 w-44 origin-top-right overflow-hidden rounded-xl border border-line-strong bg-surface-2 p-1 text-xs shadow-float"
        >
          {actions.map((action) => (
            <li key={action.label}>
              <button
                role="menuitem"
                onClick={() => {
                  onMenu(false);
                  action.run();
                }}
                className="block w-full rounded-lg px-2.5 py-2 text-left text-ink-2 transition-colors hover:bg-surface-3 hover:text-ink"
              >
                {action.label}
              </button>
            </li>
          ))}
          <li className="my-1 border-t border-line" />
          <li>
            <button
              role="menuitem"
              onClick={() => {
                onMenu(false);
                onDelete();
              }}
              className="block w-full rounded-lg px-2.5 py-2 text-left text-danger transition-colors hover:bg-danger/10"
            >
              Apagar
            </button>
          </li>
        </ul>
      ) : null}
    </li>
  );
}
