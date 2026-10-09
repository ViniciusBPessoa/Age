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
  Input,
  LoadError,
  Panel,
  Select,
  Spinner,
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
    <div className="min-h-dvh pb-14 md:pb-0">
      <header className="flex items-center gap-2 border-b border-[var(--border)] px-4 py-3">
        <h1 className="text-sm font-semibold">Estudo</h1>
        {pending.data && pending.data.length > 0 ? (
          <Badge tone="warn">{pending.data.length} sem marcar</Badge>
        ) : null}
      </header>

      <div className="mx-auto max-w-3xl space-y-5 px-4 py-5">
        {/* Marcação rápida (§8.1): "abrir o chat para dizer 'fui à
            academia' é atrito demais". Fica no topo porque é a ação com
            prazo — sessão de ontem não marcada hoje não é marcada
            nunca, e aí a recalibração de §5.9.8 perde o insumo. */}
        {pending.data && pending.data.length > 0 ? (
          <section>
            <h2 className="px-1 pb-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--muted)]">
              Como foi?
            </h2>
            <ul className="space-y-2">
              {pending.data.map((session) => (
                <Panel as="li" key={session.id} className="px-4 py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm">
                        {session.topic}
                        {session.of_parts && session.of_parts > 1 ? (
                          <span className="text-[var(--muted)]">
                            {" "}
                            · parte {session.part}/{session.of_parts}
                          </span>
                        ) : null}
                      </p>
                      <p className="mt-0.5 text-xs text-[var(--muted)]">
                        {session.subject} ·{" "}
                        {range(
                          session.scheduled_start,
                          session.scheduled_end,
                          timezone,
                        )}
                      </p>
                    </div>
                    <div className="flex gap-1.5">
                      <Button
                        size="sm"
                        variant="primary"
                        onClick={() => setMarking(session)}
                      >
                        feito
                      </Button>
                      <MarkSkipped session={session} onDone={refresh} />
                    </div>
                  </div>
                </Panel>
              ))}
            </ul>
          </section>
        ) : null}

        <section>
          <h2 className="px-1 pb-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--muted)]">
            Assuntos
          </h2>

          {subjects.isError ? (
            <LoadError
              message="Não consegui carregar seus assuntos."
              onRetry={() => void subjects.refetch()}
            />
          ) : subjects.isLoading ? (
            <p className="px-2 py-4 text-xs text-[var(--muted)]">
              <Spinner /> carregando…
            </p>
          ) : (subjects.data?.length ?? 0) === 0 ? (
            <Empty title="Nenhum plano de estudo ainda.">
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
  const ratio =
    subject.sessions_planned > 0 ? subject.sessions_done / subject.sessions_planned : 0;

  return (
    <Panel as="li">
      <button
        onClick={onToggle}
        aria-expanded={expanded}
        className="w-full px-4 py-3 text-left"
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="min-w-0 flex-1 truncate text-sm font-medium">
            {subject.name}
          </span>
          {subject.deadline ? (
            <Badge tone="warn">prova {dateOf(subject.deadline, timezone)}</Badge>
          ) : null}
          <span className="text-xs text-[var(--muted)]">
            {subject.sessions_done}/{subject.sessions_planned}
          </span>
        </div>

        {/* Barra de progresso com `role="progressbar"`: sem os atributos
            ARIA, para quem usa leitor de tela a barra não existe. */}
        <div
          role="progressbar"
          aria-valuenow={subject.sessions_done}
          aria-valuemin={0}
          aria-valuemax={subject.sessions_planned}
          aria-label={`Progresso de ${subject.name}`}
          className="mt-2 h-1.5 overflow-hidden rounded-full bg-[var(--bg)]"
        >
          <div
            className="h-full rounded-full bg-[var(--accent)] transition-[width]"
            style={{ width: `${Math.round(ratio * 100)}%` }}
          />
        </div>
      </button>

      {expanded ? (
        <div className="border-t border-[var(--border)] px-4 py-3">
          {progress ? (
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs sm:grid-cols-4">
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
            <p className="text-xs text-[var(--muted)]">
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
      <dt className="text-[10px] uppercase tracking-wide text-[var(--muted)]">
        {label}
      </dt>
      <dd className="mt-0.5 text-sm">{value}</dd>
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
      variant="ghost"
      loading={mark.isPending}
      onClick={() => mark.mutate()}
    >
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
          <Button variant="quiet" onClick={onClose}>
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
