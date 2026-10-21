/**
 * Arrastar imagem para qualquer lugar da conversa (§5.1.1).
 *
 * A zona de soltura é a `<main>` inteira — mensagens, cabeçalho e
 * composer —, não só o campo de texto. Quem arrasta uma foto mira "o
 * chat", não um retângulo de 40px de altura.
 *
 * ## O contador, e por que `dragleave` sozinho não serve
 *
 * `dragenter` e `dragleave` disparam a cada elemento FILHO que o
 * cursor cruza. Arrastar sobre uma conversa com dez mensagens dispara
 * dezenas de `dragleave` sem que o cursor tenha saído da área — e uma
 * sobreposição controlada por "entrou/saiu" pisca a cada bolha que
 * passa por baixo.
 *
 * A correção é contar entradas e saídas: a sobreposição some quando o
 * contador zera, não no primeiro `dragleave`. É o mesmo motivo pelo
 * qual `relatedTarget` não resolve sozinho — ele é `null` quando o
 * arraste sai pela janela, caso em que o contador também precisa ser
 * zerado à força (o `dragend`/`drop` no documento cuidam disso).
 *
 * ## `dragover` precisa de `preventDefault`
 *
 * Sem ele o navegador recusa a soltura e `drop` nunca dispara — o
 * cursor mostra "proibido" e nada acontece. É a pegadinha clássica da
 * API, e não há como descobri-la por teste de unidade: jsdom aceita o
 * `drop` de qualquer jeito.
 */

"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { IconImage, cx } from "@/components/ui";

/** Os mesmos quatro do backend. Filtro de conveniência, não validação. */
const TIPOS_ACEITOS = new Set(["image/jpeg", "image/png", "image/gif", "image/webp"]);

/**
 * Só as imagens do que foi solto.
 *
 * Arrastar uma pasta, um PDF ou um trecho de texto selecionado cai
 * aqui também; `DataTransfer.files` vem vazio para texto e cheio para
 * arquivo. Filtrar por tipo evita subir um `.zip` para ouvir não
 * depois — e quando NADA sobra, quem chama avisa em vez de ficar mudo.
 */
export function imagensDe(dt: DataTransfer | null): {
  imagens: File[];
  ignorados: number;
} {
  const arquivos = Array.from(dt?.files ?? []);
  const imagens = arquivos.filter((f) => TIPOS_ACEITOS.has(f.type));
  return { imagens, ignorados: arquivos.length - imagens.length };
}

/**
 * Se o que está sendo arrastado é arquivo.
 *
 * Durante o arraste o navegador ESCONDE o conteúdo por segurança:
 * `dt.files` vem vazio até o `drop`, e só `dt.types` é legível. Por
 * isso a sobreposição aparece para qualquer arquivo e o filtro por
 * tipo acontece ao soltar — mostrar "solte aqui" e depois recusar é
 * melhor que não mostrar nada e a pessoa achar que não dá.
 */
function arrastandoArquivo(dt: DataTransfer | null): boolean {
  return Array.from(dt?.types ?? []).includes("Files");
}

export function useArrastarImagem({
  ativo,
  onSoltar,
}: {
  /** `false` desliga tudo: modelo sem visão não recebe imagem. */
  ativo: boolean;
  onSoltar: (arquivos: File[], ignorados: number) => void;
}) {
  const [sobre, setSobre] = useState(false);
  // Contador de entradas menos saídas. Em `ref`: muda a cada elemento
  // cruzado, e no estado causaria um render por bolha de mensagem.
  const profundidade = useRef(0);

  const zerar = useCallback(() => {
    profundidade.current = 0;
    setSobre(false);
  }, []);

  // Soltar FORA da zona (na barra lateral, por exemplo) faz o
  // navegador abrir o arquivo e sair da aplicação — o turno em
  // andamento morre junto. Cancelar no documento inteiro é a única
  // forma de impedir, e é por isso que isto é um efeito global e não
  // um handler da `<main>`.
  useEffect(() => {
    if (!ativo) return;
    const impedir = (e: DragEvent) => {
      if (arrastandoArquivo(e.dataTransfer)) e.preventDefault();
    };
    const soltarFora = (e: DragEvent) => {
      if (arrastandoArquivo(e.dataTransfer)) e.preventDefault();
      zerar();
    };
    document.addEventListener("dragover", impedir);
    document.addEventListener("drop", soltarFora);
    document.addEventListener("dragend", zerar);
    return () => {
      document.removeEventListener("dragover", impedir);
      document.removeEventListener("drop", soltarFora);
      document.removeEventListener("dragend", zerar);
    };
  }, [ativo, zerar]);

  const props = ativo
    ? {
        onDragEnter: (e: React.DragEvent) => {
          if (!arrastandoArquivo(e.dataTransfer)) return;
          e.preventDefault();
          profundidade.current += 1;
          setSobre(true);
        },
        onDragOver: (e: React.DragEvent) => {
          if (!arrastandoArquivo(e.dataTransfer)) return;
          // Sem isto o `drop` NUNCA dispara (ver cabeçalho).
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
        },
        onDragLeave: (e: React.DragEvent) => {
          if (!arrastandoArquivo(e.dataTransfer)) return;
          profundidade.current -= 1;
          if (profundidade.current <= 0) zerar();
        },
        onDrop: (e: React.DragEvent) => {
          if (!arrastandoArquivo(e.dataTransfer)) return;
          e.preventDefault();
          zerar();
          const { imagens, ignorados } = imagensDe(e.dataTransfer);
          onSoltar(imagens, ignorados);
        },
      }
    : {};

  return { sobre, props };
}

export function SobreposicaoDeArraste({
  visivel,
  cheio,
}: {
  visivel: boolean;
  /** Já atingiu o limite de imagens por mensagem. */
  cheio?: boolean;
}) {
  if (!visivel) return null;

  return (
    <div
      /*
       * `pointer-events-none` é obrigatório: uma sobreposição que
       * recebe eventos vira ela mesma o alvo do arraste, dispara
       * `dragleave` na `<main>` e o `drop` se perde. O elemento é
       * puramente visual.
       */
      className="pointer-events-none absolute inset-0 z-30 grid place-items-center bg-surface/80 backdrop-blur-sm"
      role="status"
      aria-live="polite"
    >
      <div
        className={cx(
          "flex flex-col items-center gap-3 rounded-2xl border-2 border-dashed px-10 py-8",
          cheio ? "border-warn/50 bg-warn/5" : "border-brand/50 bg-brand/5",
        )}
      >
        <IconImage className={cx("h-8 w-8", cheio ? "text-warn" : "text-brand")} />
        <p className={cx("text-sm font-medium", cheio ? "text-warn" : "text-ink")}>
          {cheio ? "Já são 4 imagens nesta mensagem" : "Solte a imagem para anexar"}
        </p>
        {cheio ? null : (
          <p className="text-2xs text-faint">JPEG, PNG, GIF ou WebP · até 5 MB</p>
        )}
      </div>
    </div>
  );
}
