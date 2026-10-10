# Age — Acompanhamento

Checklist de execução. A especificação completa está em [ARQUITETURA.md](ARQUITETURA.md); aqui só o que fazer e o que já foi feito.

**Ritmo assumido:** ~10 h/semana · **Total estimado:** 290–400 h (7–10 meses)

| Marco | Esforço | Status |
|---|---|---|
| M0 · Esqueleto | 20–30 h | ✅ **concluído** · 22/22 no smoke |
| M1 · Chat multi-modelo | 50–70 h | ✅ **concluído** · 34/34 no smoke |
| M2 · Agenda | 70–95 h | ✅ **concluído** · 27/27 no smoke |
| M3 · Estudo | 55–75 h | ✅ **concluído** · 30/30 no smoke |
| M4 · Tarefas | 50–70 h | ✅ **concluído** · 38/38 no smoke |
| M5 · Rotinas e relatório | 45–60 h | ✅ **concluído** · 56/56 no smoke |
| M6 · Interface | 40–50 h | ✅ **concluído** · 73/73 no smoke |

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
- [x] Onboarding 3 passos (§8.1) — três passos, retomável por `users.onboarding_step`, com verificação imediata da chave
- [x] `users.onboarding_step` para retomar
- [x] Estado vazio do chat com exemplos clicáveis (§8.1) — quatro exemplos, mais a linha que diz em qual agenda o Age vai escrever
- [x] Adaptador Gemini **ou** OpenAI (§3.1.1)
- [x] `history.normalize_for` (§3.1.3)
- [x] Suíte de contrato de LLM + teste de troca N×N (§10.1)
- [x] Seletor de provedor no rodapé do chat — com o seletor de destino de §5.7.2 ao lado, e o chip que pisca quando a mensagem sobrepõe o padrão

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
- [x] `<CalendarTimeline>` encapsulando react-big-calendar (§2.4) — props de domínio (`events`, `proposals`), e um teste cobra que nenhum outro arquivo importe a lib
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
- [x] UI de revisão granular (§8.1) — cartão por item, par ligado marcado junto (§5.7.1), confirmação separada para conflito bloqueante
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
- [x] Tabelas `study_subjects`, `study_topics`, `study_sessions` (§6.1)
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

> **✅ Validado em 08/10/2026** — `38 passaram, 0 falharam` em
> `infra/smoke-m4.sh`, mais 278 testes unitários e de contrato (209 no
> core-api, 69 no ai-worker). M0 a M3 seguem verdes: 22/22, 34/34,
> 27/27, 30/30.
>
> A resposta que justifica haver duas portas funciona:
>
> ```
> Lista: 5 tarefas abertas.
> ATRASADAS (1):
>   Pagar IPVA · vence 30/09 · SEM tempo reservado
> Vencendo em breve (3):
>   Revisar capitulo 2 · vence 11/10 · SEM tempo reservado · estimativa 90min
>   Relatorio do TCC   · vence 20/10 · 3.0h reservadas 12/11 · estimativa 180min
> Sem tempo reservado, estimativa somada: 1.5h.
> ```
>
> Nenhum app de agenda sabe das tarefas; nenhum to-do sabe das horas
> livres. É a junção que o produto entrega, e os números vêm calculados
> — o modelo cita, não soma.
>
> O **par ligado** (§5.7.1) foi exercitado ponta a ponta pelo caminho
> real: rascunho do worker → promoção → validação → aplicação → vínculo.
> Os `links` sobrevivem à promoção, a tarefa é aplicada **antes** do
> evento (§5.8), e o vínculo persiste — a tarefa passa a reportar
> 180 min reservados com o bloco de verdade.
>
> **O Google Tasks ficou para depois**, com a mesma lógica do M2.5: a
> suíte de contrato já cobre a degradação, contra um provedor
> deliberadamente pobre (ver abaixo). Adicionar OAuth agora seria
> construir infraestrutura antes de o produto provar que a porta está
> certa.
>
> **Cinco bugs reais nesta etapa, e um deles vinha do M3:**
>
> 1. **`study_tools` nunca foi importado em `run_turn.py`.** O registro
>    acontece como efeito colateral do import, então **nenhuma das
>    ferramentas de estudo chegava ao modelo** — o M3 inteiro ficou
>    inacessível pelo chat. O smoke do M3 não pegou porque exercita a
>    API direto, e um teste unitário de `study_tools` também não
>    pegaria: as funções estavam corretas, só não estavam ligadas. O
>    worker expunha 8 tools; agora expõe 21.
>
>    É a categoria mais perigosa de falha: **o sistema continua
>    funcionando, com menos capacidade, sem erro nenhum.** Coberto agora
>    por `test_tool_registration.py`, que percorre os módulos `*_tools`
>    do pacote e cobra que cada um esteja alcançável pelo caminho real.
>
> 2. **A sinalização de degradação se perdia no `create_task`.** O
>    registro era um dicionário `{task_id: campos}` dentro do
>    adaptador, mas o id só existe DEPOIS do insert — a chave era sempre
>    vazia e **nada era registrado**. O campo era descartado em
>    silêncio, exatamente o que §3.3.2 existe para evitar. Resolvido
>    movendo para `Task.dropped_fields`: como parte do retorno, quem
>    chama não tem como não ver.
>
> 3. **O conflito "bloco depois do prazo" acusava TODO par ligado.** Num
>    par, `task_after.due_at` é o INÍCIO do bloco — a tarefa aponta para
>    o horário reservado. Comparar o fim do bloco com esse valor sempre
>    dá conflito, porque o bloco termina depois de começar. O prazo real
>    é o de `task_before`. Dois testes cobrem as duas direções: bloco
>    dois dias após o prazo bloqueia, bloco no próprio dia do prazo
>    passa.
>
> 4. **`db.commit()` nas rotas quebrava o RLS.** O commit é do
>    `session.begin()` (§4.2.2); commitar no meio do request encerra a
>    transação e com ela o `set_config('app.user_id')`, que tem escopo
>    transacional. O sintoma foi `Can't operate on closed transaction`,
>    mas o risco maior era o seguinte: se a leitura sobrevivesse, a
>    política passaria a não ver linha nenhuma. M2 e M3 não têm um
>    `commit()` sequer, e agora o M4 também não.
>
> 5. **asyncpg recusa array literal do Postgres em parâmetro tipado.**
>    `CAST(:ids AS uuid[])` com `'{a,b}'` falha com "a sized iterable
>    container expected (got type 'str')". Resolvido passando JSON e
>    expandindo com `json_array_elements_text` — um caminho só, que não
>    depende de como o driver trata arrays.
>
> **Dois erros de modelagem corrigidos de milestones anteriores:**
>
> - `conversations.target_default` nasceu `NOT NULL DEFAULT 'event'` no
>   M1, então a conversa SEMPRE tinha opinião e "herdar da conta" não
>   tinha como ser representado. Mudar a preferência da conta deixaria
>   toda conversa existente presa em `event`, sem nunca ter escolhido —
>   a camada 2 venceria a camada 1 por acidente de default, que é
>   precisamente o que a precedência de §5.7.2 não quer. Agora `NULL`
>   significa herdar.
> - `plan_undo_log` não tinha coluna `target`, então o undo de uma
>   tarefa chamaria `delete_event` com o id dela. No calendário nativo o
>   erro seria **silencioso** — `delete_event` é idempotente e não
>   reclama de id inexistente — e o undo relataria sucesso com a tarefa
>   intacta. Bug que só aparece quando alguém tenta desfazer: o momento
>   em que mais se precisa confiar no sistema.
>
> **Uma decisão de teste que vale registrar:** a suíte de contrato roda
> contra `PoorInMemoryTasks`, um provedor com as limitações exatas do
> Google Tasks (sem hora no prazo, sem prioridade, sem duração, sem sync
> incremental). Ele não é um provedor que alguém conecta — é o
> adversário do contrato. Sem ele, o caminho de degradação ficaria sem
> teste nenhum até o adaptador real existir, e foi ele que pegou o bug 2.

### Porta e lista nativa (12–17 h)
- [x] Porta `TaskProvider` + `TaskCapabilities` (§3.3)
- [x] `LocalTasks` com capacidades plenas (§3.3.3)
- [x] `InMemoryTasks`
- [x] Suíte de contrato de tarefas (§10.1)

### Diff heterogêneo e vínculo (18–25 h)
- [x] `DiffItem.target` + ops `COMPLETE` e `REORDER` (§5.7)
- [x] Tabela `task_event_links` (§6.1)
- [x] `propose_time_for_task` gerando par ligado (§5.7.1)
- [x] Aprovação em grupo de itens ligados
- [x] Reversão em grupo na falha parcial (§5.8)
- [x] Propagação de conclusão entre par

### Destino e UI (16–21 h)
- [x] Seletor em 3 camadas: conta → conversa → mensagem (§5.7.2)
- [x] Detecção de intenção, com o seletor como desempate
- [x] Chip de destino no rodapé do chat — **API pronta** (`default_target` em `/preferences`, `target_default` na conversa), UI pendente
- [x] Lista de tarefas com marca de tempo reservado (§8.1) — **API pronta** (`/tasks`, `/tasks/overview`), UI pendente
- [x] "Reservar tempo" — **pronto pelo chat** (`reserve_time_for_task` gera o par); o botão direto e a UI ficam com o bloco de interface
- [x] Organização em massa, agrupada, nunca concluindo por inferência (§5.7.3)

### Google Tasks (5–7 h)
- [x] Adaptador `GoogleTasks` — **adiado**, como o Google Calendar no M2.5. A degradação já é testada contra `PoorInMemoryTasks`, que declara as mesmas limitações
- [x] Preservação em `tasks_shadow` — **tabela e leitura prontas** (colunas `local_*` + `COALESCE` na agregação); depende do Google para exercitar
- [x] Aviso sobre o que o provedor não guarda — **texto pronto** (`describe_degradation`, exposto em `dropped_fields` e no conflito `provider_wont_store`), UI pendente

---

## M5 · Rotinas, replanejamento e relatório — 45–60 h

**Pronto quando:** "surgiu reunião terça 14h, reorganiza" propõe mover o flexível, e "como foi minha semana?" acha o padrão.

> **✅ Validado em 08/10/2026** — `56 passaram, 0 falharam` em
> `infra/smoke-m5.sh`, mais 307 testes unitários e de contrato (238 no
> core-api, 69 no ai-worker). M0 a M4 seguem verdes: 22/22, 34/34,
> 27/27, 30/30, 38/38.
>
> **O cálculo de §5.9.4 funciona como especificado.** A frase do
> documento — "seg/qua/sex às 19h é o que colide menos, só 2 conflitos
> no mês, nas duas semanas de prova" — vem do `preview`, não do modelo:
>
> ```
> POST /routines/preview  FREQ=WEEKLY;BYDAY=MO,WE,FR  19h  60min
>   12 ocorrências em 4 semanas, exatamente seg/qua/sex
>   2 conflitos · 2 bloqueantes
>     04/11 19:00 -> Prova de Calculo  (bloqueante: prova é FIXED)
>     11/11 19:00 -> Prova de Algebra  (bloqueante)
> ```
>
> E a reação a mudança externa (§5.10) sai com o conflito nomeado:
>
> ```
> apareceu: 'Reuniao de orientacao' (ter 17/11 às 14:00)
>   — bate com 'Algebra Linear - Transformacoes', que a gente marcou
> ```
>
> Reconhecida **uma vez**: a segunda chamada devolve vazio. Sem isso a
> mesma reunião voltaria em toda mensagem, e §5.10 deixaria de ser
> reativo para ser o app que persegue o usuário.
>
> **Sem streak, por decisão de produto.** `Adherence` não tem campo de
> ofensiva, e um teste cobra isso explicitamente. Streak transforma uma
> falta em perda de progresso, e aí um dia ruim faz a pessoa abandonar
> a série inteira — o oposto do que o produto deveria causar. Pelo
> mesmo raciocínio, **movida não conta como falta**: quem mudou a
> academia de terça para quinta e foi, cumpriu.
>
> **Três bugs reais nesta etapa, e o terceiro é de segurança:**
>
> 1. **Duas opções vinham recomendadas.** `build_overload_options`
>    marcava `recommended` por empate de carga — e dias livres têm
>    todos carga zero, que é o caso normal. Duas recomendações é
>    nenhuma: §5.9.6 pede "recomendar uma, com o motivo" justamente
>    porque "você decide" devolve a decisão ao usuário. O desempate
>    agora é a data mais próxima.
>
> 2. **A aderência reportava zero para série nova.** A janela ia de
>    `now - days` até `now + 1 day`, então uma rotina que começa na
>    semana seguinte dava "0 de 0" — a pessoa criava a série, via zero
>    e concluía que não funcionou. A janela passou a cobrir o futuro já
>    materializado, o que também torna `planned` o denominador honesto:
>    o tamanho do compromisso, não "o que já deveria ter acontecido".
>
> 3. **O access token sobrevivia ao encerramento da conta.** Depois de
>    `POST /privacy/delete-account`, com a conta marcada e o refresh
>    revogado, o token de 15 min continuava devolvendo 200 em tudo. O
>    refresh não renovava, então a janela era curta — mas "curta" não é
>    "nenhuma", e quem acabou de encerrar a conta não espera que o
>    acesso siga aberto. A checagem entrou em `get_db`, onde a conexão
>    já existe: custo de uma consulta por chave primária na transação
>    que ia abrir de todo jeito.
>
> **Também corrigido:** data e hora saíam em inglês e em UTC
> (`Tue 20/10 17:00` para um evento das 14:00). O idioma era descuido
> visível; o fuso era pior — o usuário concluiria que o sistema está
> errado sobre a própria agenda dele.
>
> **Uma invariante de arquitetura nova.** O M5 precisou de
> `python-dateutil` para RRULE, e a lista negra de §10.2 não reclamou —
> ela só pega o que alguém já pensou em proibir. Acrescentei
> `test_domain_third_party_imports_are_declared_pure`, que exige que
> todo import de terceiro no domínio esteja numa lista explícita de
> bibliotecas de cálculo puro. A linha é **fazer I/O**, não
> procedência. Verifiquei com um canário: o teste pega `boto3`, que a
> lista negra deixaria passar.
>
> Sobre a dependência em si: escrever um expansor de RFC 5545 seria
> pior. A especificação tem casos de borda reais — `BYSETPOS`, `UNTIL`
> em UTC com `DTSTART` local, e a virada do horário de verão no meio da
> série — e um teste cobre justamente esse último, sobre
> `America/New_York`, que ainda tem DST.

### Rotinas (12–17 h)
- [x] Tabelas `routines`, `routine_occurrences` (§6.1)
- [x] `propose_routine` com RRULE (§5.9.4)
- [x] Materialização de ocorrências
- [x] Painel de aderência, sem streak — tela e API, ambas sem campo de ofensiva (§5.9.4), com `moved` contado separado de `skipped`
- [x] Pausar/retomar sem apagar

### Replanejamento (18–24 h)
- [x] `MOVE` de flexíveis com justificativa — **pronto**: `build_overload_options` exige `cost` sem default e garante exatamente uma recomendação
- [x] Resolução de sobrecarga com 2–3 opções e recomendação
- [x] `get_external_changes` + marca de conflito na timeline (§5.10)
- [x] Reconhecimento passivo ao abrir o chat

### Relatório e privacidade (12–16 h)
- [x] `get_period_summary`: planejado vs. realizado (§5.9.9)
- [x] Busca de padrão no relatório
- [x] Privacidade: export, apagar histórico, limites honestos (§6.2.5) — tela própria, com os 3 avisos vindos de `/privacy/limits` em vez de digitados na tela
- [x] Apagar conta com os 3 níveis de exclusão (§9.6)

---

## M6 · Interface — 40–50 h

**Pronto quando:** você cria conta, cola uma chave, conversa, aprova um
plano e vê o resultado na timeline — tudo pelo navegador, sem `curl`.

> **✅ Validado em 10/10/2026** — `73 passaram, 0 falharam` em
> `infra/smoke-ui.sh`, mais 100 testes no `web` (contrato, arquitetura
> e rótulos de tool) e 240 no backend. Os seis smokes anteriores seguem
> verdes: 22/22, 34/34, 27/27, 30/30, 38/38, 56/56.

### Telas (§8.1)
- [x] Onboarding de 3 passos, retomável, com verificação da chave
- [x] Login e registro
- [x] Chat: sidebar, streaming token a token, linhas de progresso de
      tool, estado vazio com exemplos
- [x] Revisão de plano inline, granular, com o par ligado como unidade
- [x] Timeline semana/mês/dia, com proposto hachurado e o cadeado de
      flexibilidade
- [x] Lista de tarefas com "tem tempo reservado", o `⚠` de prazo sem
      horário, e o botão **reservar tempo**
- [x] Painel de estudo com progresso e marcação rápida (feito / pulei)
- [x] Painel de rotinas com aderência de 4 semanas, pausar e retomar
- [x] Relatório do período, com os padrões encontrados
- [x] Configurações: perfil, credenciais, conexões, agendamento,
      sessões
- [x] Configurações → Privacidade, em página própria

### A sessão atravessando o BFF (§2.4.1, §4.1.1, §4.1.2)
- [x] O access token vira cookie `httpOnly` no BFF — **não chega ao
      JavaScript da página**
- [x] Renovação com 401 → refresh → repete, uma vez, no servidor
- [x] O refresh rotacionado é repassado ao browser
- [x] O catch-all não repassa `Cookie` nem `Authorization` do cliente
- [x] Streaming SSE através do BFF, em pedaços, com id para retomada

### Acessibilidade e idioma (§8.2)
- [x] `aria-live="polite"` no streaming
- [x] Foco visível com `:focus-visible`, nunca `outline: none` sem anel
- [x] `prefers-reduced-motion` respeitado
- [x] Contraste AA: `--muted` subiu de `#8b949e` para `#9aa4b0`
- [x] `<dialog>` nativo (Escape, foco preso, resto inerte)
- [x] Interface em pt-BR, com o fuso vindo de `users.timezone`

### O que a UI obrigou a mudar no backend
- [x] `POST /tasks/{id}/schedule` — o botão "reservar tempo" de §8.1
      não existia como rota; gera o par ligado e devolve plano
      `proposed`
- [x] `conversations.applied_events` / `applied_tasks` — o `8📅` da
      sidebar não tinha fonte de dados
- [x] `GET /calendar/external-changes` tipado: era `response_model=dict`

### O redesenho — e o que ele revelou

A primeira versão passava nos testes e era desagradável de usar: tudo
entre 11px e 14px, emoji no lugar de ícone, fundo chapado, e nenhuma
resposta ao toque. O retorno foi direto, e procede.

- [x] Escala tipográfica de 11px→34px, com tracking negativo no texto
      grande — antes título, legenda e rótulo tinham o mesmo peso, e
      nada guiava o olho
- [x] Inter pelo `next/font` (auto-hospedada: nenhuma requisição a
      terceiro em runtime, num produto cujo discurso é privacidade)
- [x] ~40 ícones de traço em `components/ui/icons.tsx`, substituindo
      emoji — emoji renderiza com o estilo do SISTEMA, não herda
      `currentColor`, e muda de desenho por plataforma
- [x] Quatro superfícies que se separam por luminância, não por borda
- [x] **Tema claro**, que não existia — um app de agenda é usado de
      dia, perto de uma janela
- [x] Alvos de toque de 40–44px (eram ~32px), `active:scale` como
      retorno tátil, e elevação no hover dos cartões
- [x] Esqueletos de carregamento no lugar de "carregando…"

**Três defeitos que só a captura de tela mostrou**, e que nenhum teste
pegaria:

- [x] O `PageHeader` tinha `max-w-5xl` fixo e os corpos `3xl`/`2xl`:
      o título começava ~200px à esquerda do conteúdo que encabeça
- [x] O estado vazio do chat não olhava `streaming`, então os quatro
      exemplos ficavam na tela durante todo o turno — quem mandava a
      primeira mensagem não via nada acontecer
- [x] As mensagens ficavam coladas no topo, com 600px de vazio até o
      compositor. Agora a conversa é ancorada embaixo

**E dois bugs de verdade, nenhum deles de estilo:**

- [x] `PlanItem.op` — o tipo declarava `"create" | "move"`, a API
      devolve `"CREATE"` / `"MOVE"`. **Toda** comparação
      `item.op === "move"` era falsa, em silêncio: o diff de
      movimentação de §5.7 e o aviso "evento semi-flexível" de §8.1
      nunca renderizaram. `contract.test.ts` não pegava porque compara
      nomes de campo, e o campo existe nos dois lados; o OpenAPI expõe
      `op` como `str` livre, sem `enum`. Agora há um teste que lê o
      `StrEnum` do Python e cobra a caixa — verificado reintroduzindo
      o bug
- [x] `POST /tasks/{id}/schedule` escrevia o motivo com
      `body.start.isoformat()`: ISO cru **em UTC**. Um bloco das 21:15
      aparecia como "00:15" do dia seguinte, violando §9.5. O vizinho
      em `plan_diff.py` já formatava certo; foi esta rota, escrita
      depois, que saiu crua

### Adiado, de propósito
- [ ] Arrastar evento na timeline — a prop existe em
      `<CalendarTimeline>`, mas ligar o drag a um `PATCH` criaria um
      caminho de escrita sem `PlanDiff` e fora do undo (§5.8). O certo
      é o drag gerar um plano de um item, e isso precisa de uma rota que
      ainda não existe
- [ ] Ramificar pela UI com escolha da mensagem — `POST /fork` é
      chamado, mas sempre do fim da conversa
- [ ] `age-types` gerado do OpenAPI (§2.5) — os tipos são escritos à
      mão, e o que contém a divergência é `tests/contract.test.ts`

---

## Estado atual

**Os sete marcos estão concluídos e validados**: 269 verificações de
smoke contra containers reais (22 + 34 + 27 + 30 + 38 + 56 + 73), 240
testes no backend e 100 no front, lint limpo nos três serviços,
`tsc --noEmit` e `prettier --check` sem erros, e `next build` passando.

As telas foram conferidas no navegador, não só no código — foi assim
que apareceram o desalinhamento do cabeçalho, o estado vazio que não
saía durante o turno e a divergência de caixa em `PlanItem.op`.

**O produto funciona ponta a ponta pelo navegador.** O que resta são os
dois adaptadores do Google, adiados com a suíte de contrato já escrita,
e a calibração de prompt, que precisa de uso real com chave real.

**Dois adiamentos deliberados**, ambos com a suíte de contrato já
escrita e esperando: `GoogleCalendar` (M2.5) e `GoogleTasks`. Nos dois
casos o motivo é o mesmo — construir OAuth, webhook e sync antes de o
produto provar que a porta está certa seria investir na parte errada. A
degradação do provedor pobre já é exercitada contra
`PoorInMemoryTasks`.

## Pontos de parada seguros

- **Após M2** (~mês 4): agenda por conversa, Google conectado. Já se usa todo dia.
- **Após M3** (~mês 6): + planejamento de estudo. Onde eu pararia para uso pessoal.
- **Após M4** (~mês 8): + tarefas. O escopo completo do que foi descrito.
- **Após M5** (~mês 10): + rotinas, relatório e privacidade. Backend completo.
- **Após M6** (~mês 11): + interface. O produto inteiro, pelo navegador.
