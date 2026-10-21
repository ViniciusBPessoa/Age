/**
 * O botão de anexo e as miniaturas, no rodapé do composer (§8.1).
 *
 *   ┌──────────────────────────────────────────────────────────┐
 *   │  o que tem nesta foto?                                   │
 *   │  ┌────┐ ┌────┐                                           │
 *   │  │ 🖼 ✕│ │ ⟳  │   ← enviada · enviando                   │
 *   │  └────┘ └────┘                                           │
 *   │  📎  DeepSeek ▾   Ambos ▾                        [ ↑ ]   │
 *   └──────────────────────────────────────────────────────────┘
 *
 * ## O upload acontece na ESCOLHA, não no envio
 *
 * Quem escolhe a imagem espera que ela já esteja indo. Guardar o
 * arquivo para subir no clique de enviar travaria a interface no pior
 * momento — a pessoa já disse o que queria — e perderia tudo se
 * falhasse. Então: escolher dispara `POST /attachments`, e o envio só
 * manda os ids que já voltaram.
 *
 * O preço disso é estado de três fases por anexo (`enviando`, `pronto`,
 * `erro`), e é esse estado que o componente existe para carregar.
 *
 * ## A miniatura vem do arquivo local, não do servidor
 *
 * `URL.createObjectURL` mostra a imagem imediatamente, sem uma segunda
 * viagem para buscar o que a pessoa acabou de escolher. O `revoke` no
 * desmonte não é zelo: sem ele cada imagem escolhida fica na memória
 * da aba até a navegação, e quem anexa dez fotos de celular segura
 * dezenas de MB sem motivo.
 */

"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Button, IconDoc, IconImage, IconPaperclip, IconX, cx } from "@/components/ui";

/** 5 MB e 4 imagens: os mesmos limites do backend. */
const TAMANHO_MAXIMO = 5 * 1024 * 1024;
export const MAXIMO_DE_ANEXOS = 4;

/**
 * O que o `<input type="file">` oferece no seletor do sistema.
 *
 * É filtro de conveniência, NÃO validação: o seletor aceita "todos os
 * arquivos" em toda plataforma, e o tipo real é decidido pelos bytes no
 * servidor (`services/imagem_recebida.py`).
 */
const ACEITOS = "image/jpeg,image/png,image/gif,image/webp,application/pdf";

export type AnexoLocal = {
  /** Id do cliente, para a chave de lista e o botão de remover. */
  localId: string;
  /** Id do servidor. `null` enquanto sobe — e é por isso que o envio espera. */
  id: string | null;
  nome: string;
  /** `blob:` da imagem. Vazio em PDF, que não tem miniatura. */
  previa: string;
  estado: "enviando" | "pronto" | "erro";
  motivo?: string;
  /** PDF mostra cartão com nome e páginas, não miniatura. */
  ehPdf?: boolean;
  /** Só em PDF, depois do upload: o que o servidor conseguiu ler. */
  paginas?: number;
  paginasLidas?: number;
};

/**
 * O que `useAnexos` devolve.
 *
 * Nomeado porque o estado SOBE para o `chat-screen`: quem recebe o
 * arraste é a `<main>` inteira, e as miniaturas vivem no composer. Com
 * o hook chamado dentro do composer, os dois teriam listas separadas e
 * soltar a imagem no meio da conversa não apareceria em lugar nenhum.
 */
export type EstadoDeAnexos = {
  anexos: AnexoLocal[];
  adicionar: (arquivos: File[]) => Promise<void>;
  remover: (localId: string) => void;
  limpar: () => void;
  idsProntos: string[];
  subindo: boolean;
};

export function useAnexos(): EstadoDeAnexos {
  const [anexos, setAnexos] = useState<AnexoLocal[]>([]);
  // As prévias criadas, para revogar no desmonte. Em `ref` e não em
  // estado: revogar não é render, e pôr isto no estado faria cada
  // upload re-renderizar a lista duas vezes.
  const previas = useRef<string[]>([]);

  useEffect(
    () => () => {
      for (const url of previas.current) URL.revokeObjectURL(url);
      previas.current = [];
    },
    [],
  );

  const remover = useCallback((localId: string) => {
    setAnexos((atuais) => {
      const alvo = atuais.find((a) => a.localId === localId);
      if (alvo?.previa) URL.revokeObjectURL(alvo.previa);
      return atuais.filter((a) => a.localId !== localId);
    });
  }, []);

  const limpar = useCallback(() => {
    setAnexos((atuais) => {
      for (const a of atuais) if (a.previa) URL.revokeObjectURL(a.previa);
      return [];
    });
  }, []);

  const adicionar = useCallback(
    async (arquivos: File[]) => {
      // O corte é aqui, contra o que JÁ está na lista: escolher 3 duas
      // vezes não deve passar de 4 só porque cada lote caberia.
      const espaco = MAXIMO_DE_ANEXOS - anexos.length;
      if (espaco <= 0) return;

      for (const arquivo of arquivos.slice(0, espaco)) {
        const localId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        const ehPdf = arquivo.type === "application/pdf";
        // PDF não ganha `objectURL`: não há miniatura a mostrar, e
        // criar a URL só para revogar depois é alocação à toa.
        const previa = ehPdf ? "" : URL.createObjectURL(arquivo);
        if (previa) previas.current.push(previa);

        // O tamanho é checado antes de subir: mandar 20 MB para ouvir
        // não é gastar a banda da pessoa para nada, e num celular isso
        // é dado móvel.
        if (arquivo.size > TAMANHO_MAXIMO) {
          const mb = (arquivo.size / 1024 / 1024).toFixed(1);
          setAnexos((a) => [
            ...a,
            {
              localId,
              id: null,
              nome: arquivo.name,
              previa,
              estado: "erro",
              ehPdf,
              motivo: `${mb} MB — o limite é 5 MB`,
            },
          ]);
          continue;
        }

        setAnexos((a) => [
          ...a,
          { localId, id: null, nome: arquivo.name, previa, estado: "enviando", ehPdf },
        ]);

        try {
          const corpo = new FormData();
          corpo.append("file", arquivo);
          // `fetch` direto, sem o helper: ele põe
          // `Content-Type: application/json`, e multipart precisa que
          // o NAVEGADOR escolha o header, porque só ele sabe o
          // boundary que separa as partes.
          const res = await fetch("/api/attachments", {
            method: "POST",
            body: corpo,
          });
          if (!res.ok) {
            const erro = await res.json().catch(() => null);
            const motivo =
              erro?.detail?.message ??
              erro?.message ??
              "não deu para enviar esta imagem";
            setAnexos((a) =>
              a.map((item) =>
                item.localId === localId
                  ? { ...item, estado: "erro" as const, motivo }
                  : item,
              ),
            );
            continue;
          }
          const dados = await res.json();
          setAnexos((a) =>
            a.map((item) =>
              item.localId === localId
                ? {
                    ...item,
                    id: dados.id,
                    estado: "pronto" as const,
                    paginas: dados.page_count ?? undefined,
                    paginasLidas: dados.pages_read ?? undefined,
                  }
                : item,
            ),
          );
        } catch {
          setAnexos((a) =>
            a.map((item) =>
              item.localId === localId
                ? {
                    ...item,
                    estado: "erro" as const,
                    motivo: "sem conexão com o servidor",
                  }
                : item,
            ),
          );
        }
      }
    },
    [anexos.length],
  );

  // Só os que o servidor confirmou. Um anexo em erro não vai, e um
  // ainda subindo não pode ir: o id não existe.
  const idsProntos = anexos
    .filter((a) => a.estado === "pronto" && a.id)
    .map((a) => a.id as string);
  const subindo = anexos.some((a) => a.estado === "enviando");

  return { anexos, adicionar, remover, limpar, idsProntos, subindo };
}

export function BotaoDeAnexo({
  onEscolher,
  disabled,
  cheio,
  soPdf,
}: {
  onEscolher: (arquivos: File[]) => void;
  disabled?: boolean;
  cheio?: boolean;
  /** Modelo sem visão: só PDF, que vira texto. */
  soPdf?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);

  return (
    <>
      <input
        ref={input}
        type="file"
        accept={soPdf ? "application/pdf" : ACEITOS}
        multiple
        className="hidden"
        onChange={(e) => {
          const arquivos = Array.from(e.target.files ?? []);
          if (arquivos.length > 0) onEscolher(arquivos);
          // Limpar o valor permite escolher o MESMO arquivo de novo:
          // sem isto, remover e reescolher a mesma foto não dispara
          // `change`, e o botão parece quebrado.
          e.target.value = "";
        }}
      />
      <Button
        variant="ghost"
        size="icon"
        className="rounded-xl"
        onClick={() => input.current?.click()}
        disabled={disabled || cheio}
        title={
          cheio
            ? `No máximo ${MAXIMO_DE_ANEXOS} anexos por mensagem`
            : soPdf
              ? "Anexar PDF (este modelo não lê imagens)"
              : "Anexar imagem ou PDF"
        }
        aria-label={soPdf ? "Anexar PDF" : "Anexar imagem ou PDF"}
      >
        <IconPaperclip className="h-4 w-4" />
      </Button>
    </>
  );
}

export function MiniaturasDeAnexo({
  anexos,
  onRemover,
}: {
  anexos: AnexoLocal[];
  onRemover: (localId: string) => void;
}) {
  if (anexos.length === 0) return null;

  return (
    <ul className="flex flex-wrap gap-2 px-3 pt-3">
      {anexos.map((anexo) => (
        <li key={anexo.localId} className="relative">
          <div
            className={cx(
              "relative h-16 w-16 overflow-hidden rounded-lg border bg-surface-sunken",
              anexo.estado === "erro" ? "border-danger/50" : "border-line-strong",
            )}
          >
            {/*
             * `<img>` cru, não `next/image`: a origem é um
             * `blob:` local que o otimizador do Next não processa, e
             * passar por ele daria aviso de console sem ganho nenhum.
             */}
            {anexo.ehPdf ? (
              /*
               * PDF não tem miniatura: mostra o ícone e o nome. Sem o
               * nome, dois PDFs anexados viram dois quadrados idênticos
               * e não dá para remover o certo.
               */
              <div
                className={cx(
                  "flex h-full w-full flex-col items-center justify-center gap-1 px-1",
                  anexo.estado !== "pronto" && "opacity-40",
                )}
              >
                <IconDoc className="h-5 w-5 text-faint" />
                <span className="line-clamp-2 break-all text-center text-[9px] leading-tight text-faint">
                  {anexo.nome}
                </span>
              </div>
            ) : (
              /* eslint-disable-next-line @next/next/no-img-element */
              <img
                src={anexo.previa}
                alt={anexo.nome}
                className={cx(
                  "h-full w-full object-cover",
                  anexo.estado !== "pronto" && "opacity-40",
                )}
              />
            )}
            {anexo.estado === "enviando" ? (
              <span className="absolute inset-0 grid place-items-center" aria-hidden>
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-brand border-t-transparent" />
              </span>
            ) : null}
            {anexo.estado === "erro" ? (
              <span className="absolute inset-0 grid place-items-center" aria-hidden>
                <IconImage className="h-5 w-5 text-danger" />
              </span>
            ) : null}
          </div>

          <button
            type="button"
            onClick={() => onRemover(anexo.localId)}
            /*
             * 28px de alvo num quadro de 64px. O recomendado é 44, e
             * aqui não cabe sem cobrir a imagem que a pessoa está
             * conferindo — mas o quadro inteiro é só isto mais a
             * miniatura, então o erro de mira cai na própria imagem,
             * que não tem ação destrutiva.
             */
            className="absolute -right-1.5 -top-1.5 grid h-7 w-7 place-items-center rounded-full border border-line-strong bg-surface text-faint shadow-raised transition-colors hover:text-danger outline-none focus:border-brand focus-visible:ring-2 focus-visible:ring-brand/40"
            aria-label={`Remover ${anexo.nome}`}
          >
            <IconX className="h-3.5 w-3.5" />
          </button>

          {/*
           * O motivo do erro em texto, não só a borda vermelha: "a
           * imagem tem 7 MB" é acionável, uma borda colorida não é. E
           * `role="status"` para quem usa leitor de tela ouvir.
           */}
          {/*
           * PDF cortado no teto de páginas: a pessoa PRECISA saber que
           * o modelo não viu o documento inteiro, senão confia numa
           * resposta sobre metade do arquivo.
           */}
          {anexo.estado === "pronto" &&
          anexo.paginas &&
          anexo.paginasLidas &&
          anexo.paginasLidas < anexo.paginas ? (
            <p
              role="status"
              className="absolute left-0 top-full mt-1 w-32 text-2xs leading-tight text-warn"
            >
              li {anexo.paginasLidas} de {anexo.paginas} páginas
            </p>
          ) : null}

          {anexo.estado === "erro" && anexo.motivo ? (
            <p
              role="status"
              className="absolute left-0 top-full mt-1 w-32 text-2xs leading-tight text-danger"
            >
              {anexo.motivo}
            </p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
