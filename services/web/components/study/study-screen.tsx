/**
 * Painel de estudo (§8.1).
 *
 * "Assuntos com barra de progresso, tópicos, sessões feitas/puladas,
 * prazo. Marcar sessão como feita realimenta as estimativas (§5.9.8)."
 *
 * A realimentação é o ponto, e é por isso que o campo "quanto tempo
 * levou de verdade" não é opcional por descuido: ele é o que faz a
 * próxima estimativa melhorar. Mas também não é obrigatório — pedir o
 * número exato a quem só quer marcar "feito" transformaria o caminho
 * rápido em formulário, e aí ninguém marca nada.
 */

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  Badge,
  Button,
  Dialog,
  Empty,
  Field,
  IconCheck,
  IconChevronDown,
  IconSkip,
  IconStudy,
  Input,
  LoadError,
  PageHeader,
  Panel,
  Progress,
  Select,
  SkeletonList,
  Spinner,
  cx,
} from "@/components/ui";
import { api } from "@/lib/api-client";
import { dateOf, duration, percent, range } from "@/lib/format";
import type { PendingSession, Subject, SubjectProgress } from "@/lib/types";

export function StudyScreen({ timezone }: { timezone: string }) {
  const queryClient = useQueryClient();
  const [marking, setMarking] = useState<PendingSession | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const subjects = useQuery({
    queryKey: ["study", "subjects"],
    queryFn: () => api.get<Subject[]>("/study/subjects"),
  });

  const pending = useQuery({
    queryKey: ["study", "pending"],
    queryFn: () => api.get<PendingSession[]>("/study/sessions/pending"),
  });

  const progress = useQuery({
    queryKey: ["study", "progress", open],
    queryFn: () => api.get<SubjectProgress>(`/study/subjects/${open}`),
    enabled: open !== null,
  });

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ["study"] });
  }

  return (
    <div className="min-h-dvh pb-16 md:pb-0">
      <PageHeader
        title="Estudo"
        action={
          pending.data && pending.data.length > 0 ? (
            <Badge tone="warn">{pending.data.length} sem marcar</Badge>
          ) : undefined
        }
      />

      <div className="mx-auto max-w-3xl space-y-7 px-4 py-6 md:px-7">
        {/* Marcação rápida (§8.1): "abrir o chat para dizer 'fui à
            academia' é atrito demais". Fica no topo porque é a ação com
            prazo — sessão de ontem não marcada hoje não é marcada
            nunca, e aí a recalibração de §5.9.8 perde o insumo. */}
        {pending.data && pending.data.length > 0 ? (
          <section>
            <h2 className="eyebrow px-1 pb-2">Como foi?</h2>
            <ul className="space-y-2">
              {pending.data.map((session) => (
                <li
                  key={session.id}
                  className="card border-brand/25 bg-brand/[0.04] px-4 py-3.5"
                >
                  <div className="flex flex-wrap items-center gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">
                        {session.topic}
                        {session.of_parts && session.of_parts > 1 ? (
                          <span className="font-normal text-muted">
                            {" "}
                            · parte {session.part}/{session.of_parts}
                          </span>
                        ) : null}
                      </p>
                      <p className="mt-1 text-xs text-muted">
                        {session.subject} ·{" "}
                        <span className="tnum">
                          {range(
                            session.scheduled_start,
                            session.scheduled_end,
                            timezone,
                          )}
                        </span>
                      </p>
                    </div>
                    <div className="flex gap-2">
                      <Button
                        size="sm"
                        variant="primary"
                        onClick={() => setMarking(session)}
                      >
                        <IconCheck className="h-3.5 w-3.5" />
                        feito
                      </Button>
                      <MarkSkipped session={session} onDone={refresh} />
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        <section>
          <h2 className="eyebrow px-1 pb-2">Assuntos</h2>

          {subjects.isError ? (
            <LoadError
              message="Não consegui carregar seus assuntos."
              onRetry={() => void subjects.refetch()}
            />
          ) : subjects.isLoading ? (
            <SkeletonList rows={3} />
          ) : (subjects.data?.length ?? 0) === 0 ? (
            <Empty
              title="Nenhum plano de estudo ainda."
              icon={<IconStudy className="h-5 w-5" />}
            >
              Peça no chat: &ldquo;preciso estudar cálculo II, prova dia 20&rdquo;. O
              Age decompõe em sessões, respeita pré-requisitos e distribui com revisão
              espaçada.
            </Empty>
          ) : (
            <ul className="space-y-2">
              {subjects.data?.map((subject) => (
                <SubjectCard
                  key={subject.id}
                  subject={subject}
                  timezone={timezone}
                  expanded={open === subject.id}
                  progress={open === subject.id ? progress.data : undefined}
                  onToggle={() =>
                    setOpen((current) => (current === subject.id ? null : subject.id))
                  }
                />
              ))}
            </ul>
          )}
        </section>
      </div>

      {marking ? (
        <MarkDoneDialog
          session={marking}
          onClose={() => setMarking(null)}
          onDone={() => {
            setMarking(null);
            refresh();
          }}
        />
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------
function SubjectCard({
  subject,
  timezone,
  expanded,
  progress,
  onToggle,
}: {
  subject: Subject;
  timezone: string;
  expanded: boolean;
  progress?: SubjectProgress;
  onToggle: () => void;
}) {
  return (
    <Panel as="li" hover className="overflow-hidden">
      <button
        onClick={onToggle}
        aria-expanded={expanded}
        className="w-full px-4 py-4 text-left md:px-5"
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="min-w-0 flex-1 truncate text-base font-semibold">
            {subject.name}
          </span>
          {subject.deadline ? (
            <Badge tone="warn">prova {dateOf(subject.deadline, timezone)}</Badge>
          ) : null}
          <IconChevronDown
            className={cx(
              "h-4 w-4 text-muted transition-transform duration-200",
              expanded && "rotate-180",
            )}
          />
        </div>

        {/* O número grande é o progresso: era 12px cinza ao lado do
            título, e é a informação que a tela existe para dar. */}
        <div className="mt-3 flex items-end justify-between gap-3">
          <span className="tnum text-2xl font-semibold leading-none">
            {subject.sessions_done}
            <span className="text-base font-normal text-muted">
              /{subject.sessions_planned}
            </span>
          </span>
          <span className="text-xs text-muted">sessões feitas</span>
        </div>

        <Progress
          value={subject.sessions_done}
          max={subject.sessions_planned}
          label={`Progresso de ${subject.name}`}
          className="mt-2"
        />
      </button>

      {expanded ? (
        <div className="animate-fade-in border-t border-line bg-surface-2/40 px-4 py-4 md:px-5">
          {progress ? (
            <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
              <Stat
                label="Tópicos"
                value={`${progress.topics_completed}/${progress.topics_total}`}
              />
              <Stat label="Feito" value={duration(progress.total_done_min)} />
              <Stat label="Estimado" value={duration(progress.total_estimated_min)} />
              {/* Puladas mostradas como fato, sem juízo: §5.9.9 proíbe
                  elogio vazio, e o espelho disso é não repreender. */}
              <Stat label="Puladas" value={String(progress.sessions_skipped)} />
              <Stat label="Concluído" value={percent(progress.completion)} />
            </dl>
          ) : (
            <p className="flex items-center gap-2 text-xs text-muted">
              <Spinner /> carregando…
            </p>
          )}
        </div>
      ) : null}
    </Panel>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="eyebrow">{label}</dt>
      <dd className="tnum mt-1 text-base font-semibold">{value}</dd>
    </div>
  );
}

// ---------------------------------------------------------------
function MarkSkipped({
  session,
  onDone,
}: {
  session: PendingSession;
  onDone: () => void;
}) {
  const mark = useMutation({
    mutationFn: () =>
      api.post(`/study/sessions/${session.id}/mark`, { status: "skipped" }),
    onSuccess: onDone,
  });

  return (
    <Button
      size="sm"
      variant="secondary"
      loading={mark.isPending}
      onClick={() => mark.mutate()}
    >
      {mark.isPending ? null : <IconSkip className="h-3.5 w-3.5" />}
      pulei
    </Button>
  );
}

function MarkDoneDialog({
  session,
  onClose,
  onDone,
}: {
  session: PendingSession;
  onClose: () => void;
  onDone: () => void;
}) {
  const planned = Math.round(
    (new Date(session.scheduled_end).getTime() -
      new Date(session.scheduled_start).getTime()) /
      60000,
  );
  const [actual, setActual] = useState(String(planned));
  const [difficulty, setDifficulty] = useState("");

  const mark = useMutation({
    mutationFn: () =>
      api.post(`/study/sessions/${session.id}/mark`, {
        status: "done",
        actual_min: actual ? Number(actual) : undefined,
        difficulty: difficulty ? Number(difficulty) : undefined,
      }),
    onSuccess: onDone,
  });

  return (
    <Dialog
      open
      onClose={onClose}
      title={`Feito · ${session.topic}`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button
            variant="primary"
            loading={mark.isPending}
            onClick={() => mark.mutate()}
          >
            Registrar
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field
          label="Quanto tempo levou"
          htmlFor="actual"
          hint={`Planejado: ${duration(planned)}. É o que recalibra as próximas estimativas.`}
        >
          <Input
            id="actual"
            type="number"
            min={1}
            max={600}
            value={actual}
            onChange={(e) => setActual(e.target.value)}
          />
        </Field>

        <Field label="Dificuldade" htmlFor="difficulty" hint="Opcional.">
          <Select
            id="difficulty"
            value={difficulty}
            onChange={(e) => setDifficulty(e.target.value)}
          >
            <option value="">não dizer</option>
            <option value="1">1 — tranquilo</option>
            <option value="2">2</option>
            <option value="3">3 — na média</option>
            <option value="4">4</option>
            <option value="5">5 — difícil</option>
          </Select>
        </Field>
      </div>
    </Dialog>
  );
}
