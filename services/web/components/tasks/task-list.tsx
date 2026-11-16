/**
 * Lista de tarefas (§8.1).
 *
 * "Cada tarefa mostra se **tem tempo reservado** (§5.7.1) — é a
 * informação que diferencia o Age de um to-do comum":
 *
 *   ☐  Relatório do TCC              vence 20/10
 *      🕐 3h reservadas · qui 14:00–17:00
 *   ☐  Revisar capítulo 2            vence 10/10  ⚠
 *      sem tempo reservado            [reservar tempo]
 *
 * O aviso em tarefa com prazo próximo e sem tempo reservado "é o sinal
 * que o produto existe para dar". Sem ele a lista é uma lista, e o
 * cruzamento das duas portas (§3.2 e §3.3) não aparece em lugar nenhum
 * da tela.
 *
 * Esse sinal era um `⚠` solto no meio do título. Agora o cartão inteiro
 * ganha borda âmbar e o botão **reservar tempo** fica visível na linha
 * de baixo: o aviso que o produto existe para dar não pode depender de
 * notar um glifo de 13px.
 */

"use client";

import {
  Badge,
  Button,
  IconAlert,
  IconCheck,
  IconClock,
  IconPlus,
  IconEdit,
  IconTrash,
  cx,
} from "@/components/ui";
import { daysUntil, dueLabel, duration, range } from "@/lib/format";
import type { Priority, Task } from "@/lib/types";

const PRIORITY_TONE: Record<Priority, "neutral" | "brand" | "warn" | "danger"> = {
  low: "neutral",
  normal: "neutral",
  high: "warn",
  urgent: "danger",
};

const PRIORITY_LABEL: Record<Priority, string> = {
  low: "baixa",
  normal: "normal",
  high: "alta",
  urgent: "urgente",
};

export function TaskRow({
  task,
  timezone,
  busy,
  omitir,
  onToggle,
  onSchedule,
  onDelete,
  onEdit,
}: {
  task: Task;
  timezone: string;
  busy?: boolean;
  /**
   * O que o CABEÇALHO do grupo já diz, e o item não precisa repetir.
   *
   * Agrupado por horário, cada item repetia "1h reservada · terça
   * 20/10 · 04:05–05:05" embaixo de um cabeçalho que dizia a mesma
   * coisa; agrupado por tema, cada item repetia a etiqueta do tema.
   * Isso só apareceu vendo a tela — nenhum teste olha redundância.
   */
  omitir?: { tema?: string; bloco?: string };
  onToggle: (task: Task) => void;
  onSchedule: (task: Task) => void;
  onDelete: (task: Task) => void;
  /** Abre o diálogo de edição. Opcional: nem toda lista oferece. */
  onEdit?: (task: Task) => void;
}) {
  const done = task.status === "done";
  /* Agrupado por horário, o bloco do cabeçalho sai do item; os OUTROS
     ficam, porque "esta tarefa também tem hora na quarta" é informação
     que o cabeçalho daquele grupo não dá. */
  const blocos = task.reserved.filter((b) => b.event_ref_id !== omitir?.bloco);
  const hasReserved = blocos.length > 0;

  /**
   * O aviso de §8.1: prazo próximo (ou passado) e nenhum tempo
   * reservado.
   *
   * `is_overdue` vem do backend porque depende do fuso e do "prazo só
   * com data vale até o fim do dia" — regra de domínio, não de
   * apresentação (§9.5).
   */
  const agora = new Date();
  /* Sobre TODOS os blocos, não só os visíveis: o omitido pelo cabeçalho
     também conta para saber se ainda há horário pela frente. */
  const horarioPassou = !done && situacaoDoHorario(task, agora) === "passou";
  const needsTime =
    horarioPassou ||
    (!done && !hasReserved && (task.is_overdue || isSoon(task, timezone)));

  return (
    <li
      className={cx(
        "group card card-hover relative overflow-hidden px-4 py-3.5",
        done && "opacity-60",
        needsTime && "border-warn/35",
      )}
    >
      {/* A faixa lateral: quem varre a lista encontra o que precisa de
          atenção sem ler nada. */}
      {needsTime ? (
        <span
          aria-hidden
          className={cx(
            "absolute left-0 top-0 h-full w-[3px]",
            task.is_overdue ? "bg-danger" : "bg-warn",
          )}
        />
      ) : null}

      <div className="flex items-start gap-3">
        {/*
         * Caixa de marcação desenhada, não a nativa.
         *
         * A nativa tem ~13px e desenho do sistema; esta tem 20px, a cor
         * do tema e a marca de "feito" como ícone. Continua sendo
         * `<input type="checkbox">` por baixo (`peer` + `sr-only`),
         * então teclado, foco e leitor de tela seguem iguais.
         */}
        <label className="relative mt-0.5 flex shrink-0 cursor-pointer items-center">
          <input
            type="checkbox"
            checked={done}
            disabled={busy}
            onChange={() => onToggle(task)}
            aria-label={done ? `Reabrir ${task.title}` : `Concluir ${task.title}`}
            className="peer sr-only"
          />
          <span
            aria-hidden
            className={cx(
              "grid h-5 w-5 place-items-center rounded-md border-2 transition-all duration-150",
              "peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-brand",
              done
                ? "border-ok bg-ok text-canvas"
                : "border-line-strong text-transparent hover:border-muted",
            )}
          >
            <IconCheck className="h-3 w-3" strokeWidth={3} />
          </span>
        </label>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span
              className={cx(
                "text-sm font-medium",
                done ? "text-muted line-through" : "text-ink",
              )}
            >
              {task.title}
            </span>

            {task.priority && task.priority !== "normal" ? (
              <Badge tone={PRIORITY_TONE[task.priority]}>
                {PRIORITY_LABEL[task.priority]}
              </Badge>
            ) : null}

            {task.labels
              .filter((label) => label !== omitir?.tema)
              .map((label) => (
                <Badge key={label}>{label}</Badge>
              ))}
          </div>

          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
            {(task.due_at ?? task.due_date) ? (
              <span
                className={cx(
                  "tnum font-medium",
                  task.is_overdue && !done ? "text-danger" : "text-muted",
                )}
              >
                {dueLabel(task.due_at ?? `${task.due_date}T23:59:59Z`, timezone)}
              </span>
            ) : (
              <span className="text-faint">sem prazo</span>
            )}

            {task.estimated_min ? (
              <span className="text-muted">{duration(task.estimated_min)}</span>
            ) : null}
          </div>

          {/* A linha que é o ponto desta tela. */}
          <div className="mt-2">
            {hasReserved || horarioPassou ? (
              <div className="space-y-1">
                {blocos.map((block) => {
                  /* Bloco que já acabou não é tempo reservado: é tempo
                     que passou. Em verde ele dizia "resolvido" para uma
                     tarefa atrasada 31 dias — visto na conta real. */
                  const passou = new Date(block.end) <= agora;
                  return (
                    <p
                      key={block.event_ref_id}
                      /* `ok` e não `brand`: tempo reservado é estado
                         resolvido, e a cor da marca é a mesma dos botões
                         "reservar tempo" ao lado. Na mesma cor deles, o
                         que já está feito disputava atenção com o que
                         ainda falta — e é só o que falta que pede ação. */
                      className={cx(
                        "flex items-center gap-1.5 text-xs",
                        passou ? "text-muted" : "font-medium text-ok",
                      )}
                    >
                      <IconClock className="h-3.5 w-3.5 shrink-0" />
                      <span className="tnum">
                        {passou
                          ? `${duration(block.minutes)} · ${range(block.start, block.end, timezone)} · já passou`
                          : `${duration(block.minutes)} reservad${block.minutes === 60 ? "a" : "as"} · ${range(block.start, block.end, timezone)}`}
                      </span>
                    </p>
                  );
                })}
                {horarioPassou ? (
                  <div className="flex flex-wrap items-center gap-2 pt-0.5">
                    <span className="flex items-center gap-1.5 text-xs font-medium text-warn">
                      <IconAlert className="h-3.5 w-3.5 shrink-0" />o horário passou e a
                      tarefa segue aberta
                    </span>
                    <Button
                      size="sm"
                      variant="primary"
                      onClick={() => onSchedule(task)}
                    >
                      <IconPlus className="h-3.5 w-3.5" />
                      reservar de novo
                    </Button>
                  </div>
                ) : null}
              </div>
            ) : done || task.reserved.length > 0 ? null : (
              /* `task.reserved`, não `blocos`: se o único bloco foi
                 omitido porque o cabeçalho já o diz, a tarefa TEM
                 horário. Dizer "sem tempo reservado" e oferecer
                 reservar seria o contrário da verdade. */
              <div className="flex flex-wrap items-center gap-2">
                <span
                  className={cx(
                    "flex items-center gap-1.5 text-xs",
                    needsTime ? "font-medium text-warn" : "text-muted",
                  )}
                >
                  {needsTime ? <IconAlert className="h-3.5 w-3.5 shrink-0" /> : null}
                  sem tempo reservado
                </span>
                <Button
                  size="sm"
                  variant={needsTime ? "primary" : "secondary"}
                  onClick={() => onSchedule(task)}
                >
                  <IconPlus className="h-3.5 w-3.5" />
                  reservar tempo
                </Button>
              </div>
            )}
          </div>

          {/* §3.3.2: a degradação aparece NO ITEM, não só na tela de
              conexão — "o usuário precisa saber ao olhar a tarefa". */}
          {task.dropped_fields.length > 0 ? (
            <p className="mt-2 flex items-start gap-1.5 rounded-lg bg-warn/10 px-2 py-1.5 text-xs leading-relaxed text-warn">
              <IconAlert className="mt-px h-3 w-3 shrink-0" />
              <span>
                O {task.provider_name ?? "provedor"} não guarda{" "}
                {task.dropped_fields.join(", ")} — isso fica só aqui no Age.
              </span>
            </p>
          ) : null}
        </div>

        {/*
         * Editar, ao lado de apagar. Aparece no hover como o apagar
         * já aparecia, mas com `focus-visible` também: quem navega por
         * teclado precisa alcançar os dois.
         */}
        {onEdit ? (
          <button
            onClick={() => onEdit(task)}
            aria-label={`Editar ${task.title}`}
            className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-muted opacity-0 transition-all hover:bg-surface-3 hover:text-ink focus-visible:opacity-100 group-hover:opacity-100"
          >
            <IconEdit className="h-3.5 w-3.5" />
          </button>
        ) : null}

        <button
          onClick={() => onDelete(task)}
          aria-label={`Apagar ${task.title}`}
          className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-muted opacity-0 transition-all hover:bg-danger/10 hover:text-danger focus-visible:opacity-100 group-hover:opacity-100"
        >
          <IconTrash className="h-3.5 w-3.5" />
        </button>
      </div>
    </li>
  );
}

/**
 * A tarefa tem horário pela frente, só horário que já passou, ou nenhum?
 *
 * "passou" é o caso que esta função existe para separar: a tarefa tinha
 * bloco reservado, o bloco acabou e ela não foi concluída. Antes ela
 * caía junto com "tem horário" e aparecia em verde, como resolvida — o
 * "Relatório do TCC" atrasado 31 dias com "quinta 15/10 09:15" ao lado.
 *
 * Bloco em andamento conta como pela frente: ainda dá para fazer nele.
 */
export function situacaoDoHorario(
  task: Pick<Task, "reserved">,
  agora: Date = new Date(),
): "futuro" | "passou" | "sem" {
  if (task.reserved.length === 0) return "sem";
  return task.reserved.some((b) => new Date(b.end) > agora) ? "futuro" : "passou";
}

/**
 * Prazo dentro de 7 dias — o mesmo horizonte que `/tasks/overview` usa.
 *
 * Calcula sobre data, e não sobre o texto de `dueLabel`. A primeira
 * versão fazia regex no rótulo ("vence em \d+ dias"), o que acopla uma
 * decisão a uma string de interface: mudar "vence em 3 dias" para "em 3
 * dias" apagaria o aviso de toda a lista sem erro nenhum, e o sinal que
 * §8.1 chama de "o sinal que o produto existe para dar" desapareceria
 * em silêncio.
 */
function isSoon(task: Task, timezone: string): boolean {
  const iso = task.due_at ?? (task.due_date ? `${task.due_date}T23:59:59Z` : null);
  if (!iso) return false;
  return daysUntil(iso, timezone) <= 7;
}
