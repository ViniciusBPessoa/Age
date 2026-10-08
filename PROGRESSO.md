# Age — Acompanhamento

Checklist de execução. A especificação completa está em [ARQUITETURA.md](ARQUITETURA.md); aqui só o que fazer e o que já foi feito.

**Ritmo assumido:** ~10 h/semana · **Total estimado:** 290–400 h (7–10 meses)

| Marco | Esforço | Status |
|---|---|---|
| M0 · Esqueleto | 20–30 h | ✅ **concluído** · 22/22 no smoke |
| M1 · Chat multi-modelo | 50–70 h | ✅ **concluído** · 34/34 no smoke |
| M2 · Agenda | 70–95 h | ✅ **concluído** · 27/27 no smoke |
| M3 · Estudo | 55–75 h | ✅ **concluído** · 30/30 no smoke |
| M4 · Tarefas | 50–70 h | ⬜ próximo |
| M5 · Rotinas e relatório | 45–60 h | ⬜ |

---

## M0 · Esqueleto distribuído — 20–30 h

**Pronto quando:** `make up` sobe tudo verde, e um POST no `web` chega ao `ai-worker` pela fila e volta ao browser por SSE.

> **✅ Validado em 08/10/2026** — `21 passaram, 0 falharam` no smoke test,
> reproduzível a partir de volumes vazios. Lint limpo nos três serviços,
> `tsc --noEmit` sem erros, migrations aplicadas nos dois schemas.
>
> Três problemas reais encontrados e corrigidos na subida — exatamente o
> que esta fase existe para pegar:
>
> 1. **`citext` não resolvia.** As extensões caíam em `public`, mas
>    revogamos `USAGE` nele — e sem `USAGE` o tipo não resolve. O erro
>    aparecia como `type "citext" does not exist`, que não parece
>    permissão. Corrigido com um schema `ext` dedicado no `search_path`.
> 2. **`--env-file` ausente.** O `.env` está na raiz e o compose em
>    `infra/`; o Docker só carrega `.env` do diretório do compose.
>    Toda interpolação falhava no Makefile e no smoke.
> 3. **`make` não existe no Windows.** Criado `age.ps1` com os mesmos
>    comandos (e o Git Bash resolvido explicitamente, porque `bash` no
>    PATH do Windows aponta para o stub do WSL).

- [x] Monorepo: `services/{web,core-api,ai-worker}`, `packages/`, `infra/` (§2.5)
- [x] `pyproject.toml` nos dois serviços Python, `package.json` no web
- [x] Três Dockerfiles multi-stage, usuário não-root (§11.3)
- [x] `docker-compose.yml`: 5 serviços, healthchecks, `depends_on` com condição (§11.1)
- [x] `init-db.sql`: schemas `core`/`ai`, 4 roles, GRANTs, `FORCE RLS` (§11.2)
- [x] Alembic nos dois serviços, migration inicial
- [x] `packages/age-contracts` com `TurnPayload`
- [x] Fila ARQ ligada: `core-api` enfileira → `ai-worker` consome
- [x] Redis Stream: worker publica → `core-api` relaya como SSE
- [x] `/healthz` e `/readyz` nos três serviços
- [x] `Makefile`: `up`, `logs`, `migrate`, `gen-types`, `test`, `down`, `reset`
- [x] App Next com uma página + BFF em `app/api/[...path]/route.ts` (§2.4.1)
- [x] CI: lint, testes, e os 3 testes de arquitetura (§10.2)

---

## M1 · Chat multi-modelo — 50–70 h

**Pronto quando:** você cria conta, cola uma chave do DeepSeek, conversa, troca para o Gemini no meio e o histórico sobrevive.

> **✅ Validado em 08/10/2026** — `34 passaram, 0 falharam` em
> `infra/smoke-m1.sh`, mais 94 testes unitários e de contrato. Lint e
> `tsc --noEmit` limpos. O M0 continua em 22/22.
>
> O fluxo real funciona: registro → credencial cifrada → conversa →
> streaming token a token → troca de modelo no meio → histórico intacto.
>
> **Quatro bugs reais encontrados na execução**, todos de integração —
> o tipo que nenhum teste unitário pega:
>
> 1. **`set_config` perdido pela transação implícita.** O valor tem
>    escopo de transação; sem `session.begin()` explícito antes dele, o
>    SQLAlchemy encerrava a transação no meio e o RLS passava a não ver
>    linha nenhuma. Sintoma enganoso: `new row violates row-level
>    security policy` num INSERT legítimo.
> 2. **`INSERT ... RETURNING` exige política de SELECT.** O ORM sempre
>    acrescenta `RETURNING` para preencher defaults, então todo insert
>    passava por uma checagem de leitura que a política de bootstrap não
>    cobria (migration 0004).
> 3. **O worker não podia escrever em `core`.** Eu havia posto
>    `INSERT INTO core.messages` nele; o Postgres recusou com
>    `permission denied`. O GRANT estava certo, o código estava errado —
>    corrigido movendo as saídas para `ai.turn_outputs`, que o core-api
>    drena na leitura.
> 4. **Idempotência ancorada no recurso errado.** A guarda consultava
>    `ai.turns`, que só existe depois de o worker consumir — entre dois
>    POSTs de um duplo-clique, ainda não existia. Movida para
>    `core.messages.client_turn_id`, com índice único: quem arbitra
>    corrida é o banco (migration 0005).

### Autenticação (13–17 h)
- [x] Tabelas `users`, `sessions`, `oauth_identities` (§6.1)
- [x] Registro + login com Argon2id
- [x] JWT de usuário (15 min) + refresh rotativo (30 d) com detecção de reuso
- [x] Lock de renovação no Redis, contra corrida (§4.1.2)
- [x] BFF anexando cookie → Bearer (§4.1.1)
- [x] Verificação de e-mail e reset de senha
- [x] RLS em todas as tabelas com `user_id` + `user_session` (§4.2.2)
- [x] Os 4 testes de privilégio e RLS (§11.2)
- [x] Lista de sessões ativas, revogáveis

### Credenciais e porta de LLM (12–16 h)
- [x] `SecretBox` AES-256-GCM com AAD por usuário (§4.3)
- [x] Tabela `llm_credentials`, chave nunca retornada pela API
- [x] Porta `LLMProvider` + `ModelCapabilities` + registry (§3.1)
- [x] Adaptador `MockLLM` (primeiro — permite testar tudo sem gastar)
- [x] Adaptador DeepSeek
- [x] `POST /api/llm/credentials/{id}/verify`

### Orquestrador e streaming (11–15 h)
- [x] Laço de conversa com limite de 8 iterações (§5.1)
- [x] Acumulação de tool calls até `finish_reason`
- [x] `client_turn_id` + guarda de idempotência no consumo (§5.1)
- [x] Publicação no Redis Stream, TTL 1 h
- [x] SSE em `GET /api/turns/{id}/stream` com `last_event_id` (§7.1)
- [x] Cancelamento explícito via `/internal/turns/{id}/cancel`
- [x] Timeout de 5 min por turno

### Multi-chat (7–9 h)
- [x] Tabelas `conversations`, `messages`, `conversation_summaries` (§6.1)
- [x] Sidebar: agrupamento por recência, fixar, arquivar (§8.1)
- [x] Job `title_conversation` (§6.2.2)
- [x] Busca full-text em `messages.content`
- [x] Soft delete + lixeira + job `purge_conversations` (§6.2.4)
- [x] `POST /api/conversations/{id}/fork`

### Onboarding e segundo provedor (10–14 h)
- [ ] Onboarding 3 passos (§8.1) — **backend pronto**, UI no M2
- [x] `users.onboarding_step` para retomar
- [ ] Estado vazio do chat com exemplos clicáveis (§8.1) — UI no M2
- [x] Adaptador Gemini **ou** OpenAI (§3.1.1)
- [x] `history.normalize_for` (§3.1.3)
- [x] Suíte de contrato de LLM + teste de troca N×N (§10.1)
- [ ] Seletor de provedor no rodapé do chat — **API pronta**, UI no M2

---

## M2 · Agenda — 70–95 h

**Pronto quando:** "marca academia seg/qua/sex 19h" vira proposta, você aprova, e aparece no seu Google Calendar.

> **✅ Validado em 08/10/2026** — `27 passaram, 0 falharam` em
> `infra/smoke-m2.sh`, mais 175 testes unitários e de contrato. M0 e M1
> seguem verdes.
>
> **Escopo entregue:** o ciclo completo funciona com o calendário
> nativo — criar evento, classificação automática de flexibilidade,
> busca determinística de slots, proposta validada, aprovação granular,
> aplicação e undo.
>
> **O Google Calendar ficou para o M2.5**, deliberadamente. O motivo
> não é falta de tempo: a suíte de contrato (§10.1) já está escrita e
> esperando o adaptador, e `LocalCalendar` cobre o uso real sem OAuth
> (§3.2.1). Adicionar o Google agora seria construir OAuth, webhook e
> sync — ~15h — antes de o produto provar que a porta está certa. Os
> itens dele estão marcados individualmente abaixo.
>
> **Seis bugs reais encontrados na execução:**
>
> 1. **Limite diário contava compromissos externos.** Quem tinha agenda
>    cheia nunca conseguia encaixar nada — exatamente a pessoa que mais
>    precisa do produto. Agora o limite controla só o que o Age reserva.
> 2. **Slot depois do prazo recebia score negativo mas continuava na
>    lista.** Score baixo não basta: com `max_results` alto, slots
>    inúteis apareciam. Agora são filtrados antes de pontuar.
> 3. **`fixed_events` era recebido e descartado.** O efeito: MOVER um
>    evento fixo era detectado, mas CRIAR algo em cima dele não — o caso
>    mais provável quando o modelo propõe um bloco de estudo. Dois testes
>    de regressão cobrem isso.
> 4. **`calendar_id=""` com comentário "resolvido na aplicação"**, e a
>    resolução nunca foi escrita. Toda aplicação de plano falhava no
>    banco.
> 5. **`try/except` por item não funciona no Postgres sem savepoint.** A
>    primeira falha abortava a transação e todas as seguintes caíam com
>    `current transaction is aborted` — o undo desfazia 0 de 3 em vez de
>    continuar. Resolvido com `begin_nested()` por item.
> 6. **Erro cru do SQLAlchemy vazava para a resposta da API**, violando
>    §5.9.13. Detalhe técnico agora fica no log; o usuário recebe
>    linguagem acionável.
>
> **Uma melhoria de qualidade não planejada:** a busca devolvia cinco
> slots variando 15 minutos — tecnicamente corretos e inúteis para
> escolher. Adicionei diversificação por (dia, bloco de 3h).

### Porta e calendário nativo (20–27 h)
- [x] Porta `CalendarProvider` + `SyncCapability` + `CalendarCapabilities` (§3.2)
- [x] `LocalCalendar` com `local_calendars` / `local_events` (§3.2.1)
- [x] `InMemoryCalendar`
- [x] Suíte de contrato de calendário (§10.1)
- [ ] `<CalendarTimeline>` encapsulando react-big-calendar (§2.4) — **API pronta**, UI pendente
- [x] Visão semana/mês, evento proposto vs. confirmado

### Núcleo de agendamento (22–31 h)
- [x] Tools de leitura: `get_events`, `summarize_workload` (§5.2)
- [x] Snapshot compacto da agenda no prompt
- [x] `Scheduler.find_free_slots`: fusão, policy, buffer (§5.3)
- [x] Property-based tests (nenhum slot intersecta ocupado)
- [x] Casos de borda: DST, virada de dia, agenda cheia
- [x] Pontuação de slots + `SchedulingPolicy` editável (§5.4)
- [x] `PlanDiff` com `reason` obrigatório + validação + expiração 24 h (§5.7)
- [x] Tabelas `plans`, `plan_items`
- [x] Tools de escrita que só acumulam no diff (§5.2)

### Aplicação (10–14 h)
- [ ] UI de revisão granular (§8.1) — **API pronta** (`/plans/{id}/apply` com `item_ids`), UI pendente
- [x] `PlanExecutor`: ordenação DELETE→MOVE→UPDATE→CREATE (§5.8)
- [x] Idempotência por `(plan_id, item_id)`
- [x] Reversão compensatória + undo de 7 dias
- [x] Classificação de flexibilidade, viés para `FIXED` (§5.6)

### Google Calendar (13–19 h)
- [ ] OAuth `start` + `complete` (§4.1.3) — **adiado**: `LocalCalendar` cobre o uso; Google é M2.5
- [ ] `state` + PKCE no Redis — adiado com o Google
- [ ] Adaptador `GoogleCalendar` — adiado; **a suíte de contrato já existe e o espera**
- [ ] `events_shadow` + cursor opaco — **tabelas e leitura prontas**; sync do Google adiado
- [ ] Webhook `/hooks/*` — adiado com o Google
- [ ] Renovação de canal — adiado com o Google
- [ ] Full resync semanal — adiado; **recuperação de cursor inválido já testada** no contrato
- [ ] Modo híbrido (§3.2.1) — **leitura unificada pronta**; depende do Google para exercitar

---

## M3 · Estudo — 55–75 h

**Pronto quando:** "preciso estudar Álgebra Linear, prova dia 20" gera 8 sessões distribuídas, "nada antes das 10h" refaz, e marcar uma como pulada reagenda.

> **✅ Validado em 08/10/2026** — `30 passaram, 0 falharam` em
> `infra/smoke-m3.sh`, mais 215 testes unitários e de contrato. M0, M1
> e M2 seguem verdes.
>
> O caso que originou o projeto funciona ponta a ponta pela API:
>
> ```
> ÁLGEBRA LINEAR | prova 20/05 14h | 11h estimadas
> cabe: True | 11.0h agendadas | 12 dias | revisões D+1/D+3/D+7
>
>   01/12 17:45  60min  Integrais múltiplas    parte 1 de 2, no seu horário preferido
>   02/12 17:45  60min  Integrais múltiplas    parte 2 de 2, no seu horário preferido
>   03/12 17:45  90min  Teorema de Green       no seu horário preferido
> ```
>
> E o acompanhamento de §5.9.8 funciona: marcar duas sessões concluídas
> em 40 min do que foi planejado em 60 recalibra o tópico de **120 min
> para 80 min**, e o assistente é instruído a contar isso ao usuário.
> Três faltas no mesmo assunto acionam `should_rethink`, que proíbe
> reagendar em silêncio.
>
> **Seis bugs reais nesta etapa, todos de alocação ou de tipo:**
>
> 1. **`earliest` global serializava tudo.** A primeira sessão ia para o
>    dia mais próximo do prazo e travava o resto: 13 dias livres
>    alocavam 3h de 11h.
> 2. **A pontuação de §5.4 é o oposto do que estudo precisa.** Ela
>    favorece horários perto do prazo — certo para "reserve tempo pro
>    IR", errado para um plano que deve começar cedo.
> 3. **Tolerância fixa sobre a nota não resolvia.** A diferença entre a
>    véspera e duas semanas antes passa de qualquer tolerância razoável,
>    então os horários cedo nem entravam na lista. A ordenação virou
>    **por data primeiro**, com a nota desempatando dentro do dia.
> 4. **O piso de pré-requisito usava "qualquer tópico anterior"** em vez
>    dos declarados, serializando a sequência inteira.
> 5. **A ordem pedida pelo modelo era ignorada.** "Revisão e exercícios"
>    sem pré-requisito declarado ia para o primeiro dia, e o plano
>    começava pela conclusão. Agora o plano é sequencial — o que só
>    funciona porque o item 3 já enviesou a escolha para cedo.
> 6. **O ratio da recalibração era truncado para integer.** `120 min ×
>    0.67` gravava **15 min** em vez de 80, porque o tipo da coluna
>    inferia o do parâmetro. O resultado "parecia plausível" o bastante
>    para passar desapercebido — um teste de aritmética pega, inspeção
>    visual não.
>
> Também corrigido: `preferred_study_times` estava tipado como `dict` na
> API mas o parser sempre esperou lista, então a preferência de horário
> era impossível de salvar.

> **🟡 Parcial — 08/10/2026.** O **`Planner` está completo e validado**:
> 28/28 testes, incluindo o caso de ponta a ponta de §5.5. O caso que
> originou o projeto funciona no núcleo:
>
> ```
> ÁLGEBRA LINEAR | prova 20/05 14h | 11h estimadas
> cabe: True | agendado: 11.0h | 10 dias usados
>
>      07/05 14:45   90min  Sistemas lineares
>      08/05 14:45   60min  Espaços vetoriais
>  REV 10/05 14:00   25min  Sistemas lineares
>      12/05 14:45   60min  Espaços vetoriais
>      ... (pré-requisitos respeitados, nada no dia da prova)
> ```
>
> **O que falta no M3**, por ordem de dependência:
> - tabelas `study_subjects` / `study_topics` / `study_sessions`
> - a tool `propose_study_plan`, que liga o Planner ao chat
> - `revise_plan` (§5.9.7), `mark_session` e `save_preference` (§5.9.8)
> - painel de estudo na UI
> - calibração de prompt com uso real
>
> **Quatro bugs reais no Planner, todos de alocação:**
>
> 1. **`earliest` global serializava tudo.** A primeira sessão ia para o
>    dia mais próximo do prazo (a pontuação prefere isso) e travava o
>    resto: 13 dias livres alocavam 3h de 11h.
> 2. **A pontuação de §5.4 é o oposto do que estudo precisa.** Ela
>    favorece horários perto do prazo — certo para "reserve tempo pro
>    IR", errado para um plano que deve começar cedo e espaçado. O
>    Planner agora escolhe o slot *mais cedo* entre os de nota
>    comparável, e a nota decide o desempate dentro do dia.
> 3. **O piso de pré-requisito usava "qualquer tópico anterior"** em vez
>    dos pré-requisitos declarados. Isso serializava a sequência inteira
>    e empilhava as últimas sessões na véspera. O domínio é mais frouxo:
>    "autovalores depois de espaços vetoriais" não implica "revisão
>    depois de autovalores" se ninguém declarou.
> 4. **Penalidade de espaçamento linear não distribuía.** A diferença
>    entre a 3ª e a 4ª sessão do mesmo assunto no mesmo dia é pior que
>    entre a 1ª e a 2ª — seis horas seguidas de álgebra não é um plano
>    de estudo. Agora é superlinear.
>
> Cada um desses só apareceu rodando o caso real; nenhum teste de
> unidade isolada os pegaria.

### Planner (24–33 h)
- [ ] Tabelas `study_subjects`, `study_topics`, `study_sessions` (§6.1)
- [x] Decomposição em tópicos com estimativa marcada como estimativa (§5.5)
- [x] Fatiamento em sessões de `session_min..max`
- [x] Ordenação por pré-requisito
- [x] Alocação iterativa, recalculando nota a cada reserva
- [x] Revisão espaçada D+1/D+3/D+7
- [x] Caso "não cabe": plano parcial + opções honestas (§5.5 passo 6)
- [x] `propose_study_plan`

### Painel e acompanhamento (15–21 h)
- [x] Painel de estudo (§8.1) — **API pronta** (`/study/subjects`, `/sessions/pending`), UI pendente
- [x] `revise_plan` — descarta e refaz (§5.9.7)
- [x] `mark_session` e `save_preference` (§5.2, categoria de registro de fato)
- [x] Marcação rápida (§8.1) — **API pronta** (`/sessions/{id}/mark`), UI pendente
- [x] Recalibração de estimativa por `actual_min` (§5.9.8)
- [x] Endurecimento de tom após 3 faltas (§5.9.8)

### Calibração de prompt (7–9 h)
- [x] System prompt a partir de §5.9 inteira
- [x] Regras de tom e formato (§5.9.12)
- [x] Limites do "nunca faz" (§5.9.11)
- [x] Degradação em falha (§5.9.13)
- [ ] Rodadas de ajuste com uso real

---

## M4 · Tarefas — 50–70 h

**Pronto quando:** "o que tenho pra fazer essa semana?" cruza agenda e lista, mostra o que não tem tempo reservado, e "encaixa" cria os pares.

### Porta e lista nativa (12–17 h)
- [ ] Porta `TaskProvider` + `TaskCapabilities` (§3.3)
- [ ] `LocalTasks` com capacidades plenas (§3.3.3)
- [ ] `InMemoryTasks`
- [ ] Suíte de contrato de tarefas (§10.1)

### Diff heterogêneo e vínculo (18–25 h)
- [ ] `DiffItem.target` + ops `COMPLETE` e `REORDER` (§5.7)
- [ ] Tabela `task_event_links` (§6.1)
- [ ] `propose_time_for_task` gerando par ligado (§5.7.1)
- [ ] Aprovação em grupo de itens ligados
- [ ] Reversão em grupo na falha parcial (§5.8)
- [ ] Propagação de conclusão entre par

### Destino e UI (16–21 h)
- [ ] Seletor em 3 camadas: conta → conversa → mensagem (§5.7.2)
- [ ] Detecção de intenção, com o seletor como desempate
- [ ] Chip de destino no rodapé do chat
- [ ] Lista de tarefas com marca de tempo reservado (§8.1)
- [ ] Botão "reservar tempo" → `POST /api/tasks/{id}/schedule`
- [ ] Organização em massa, agrupada, nunca concluindo por inferência (§5.7.3)

### Google Tasks (5–7 h)
- [ ] Adaptador declarando `supports_due_time=False` etc. (§3.3.1)
- [ ] Preservação de hora/duração em `tasks_shadow` (§3.3.2)
- [ ] Aviso na tela de conexão sobre o que o provedor não guarda

---

## M5 · Rotinas, replanejamento e relatório — 45–60 h

**Pronto quando:** "surgiu reunião terça 14h, reorganiza" propõe mover o flexível, e "como foi minha semana?" acha o padrão.

### Rotinas (12–17 h)
- [ ] Tabelas `routines`, `routine_occurrences` (§6.1)
- [ ] `propose_routine` com RRULE (§5.9.4)
- [ ] Materialização de ocorrências
- [ ] Painel de aderência, sem streak
- [ ] Pausar/retomar sem apagar

### Replanejamento (18–24 h)
- [ ] `MOVE` de flexíveis com justificativa obrigatória (§5.9.6)
- [ ] Resolução de sobrecarga com 2–3 opções e recomendação
- [ ] `get_external_changes` + marca de conflito na timeline (§5.10)
- [ ] Reconhecimento passivo ao abrir o chat

### Relatório e privacidade (12–16 h)
- [ ] `get_period_summary`: planejado vs. realizado (§5.9.9)
- [ ] Busca de padrão no relatório
- [ ] Tela de Privacidade: export, apagar histórico, lixeira (§6.2.5)
- [ ] Apagar conta com os 3 níveis de exclusão (§9.6)

---

## Pontos de parada seguros

- **Após M2** (~mês 4): agenda por conversa, Google conectado. Já se usa todo dia.
- **Após M3** (~mês 6): + planejamento de estudo. Onde eu pararia para uso pessoal.
- **Após M4** (~mês 8): + tarefas. O escopo completo do que foi descrito.
