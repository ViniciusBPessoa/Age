# Age

Assistente de agenda conversacional. Você conversa em linguagem natural e
ele lê, propõe e escreve na sua agenda real — quebrando um assunto de
estudo em sessões, encaixando nos horários livres e reorganizando o que
for flexível quando necessário.

Nada é escrito sem você aprovar.

- **Especificação completa:** [ARQUITETURA.md](ARQUITETURA.md)
- **Acompanhamento:** [PROGRESSO.md](PROGRESSO.md)

**Estado atual: M3 concluído** — o caso de uso central funciona.
Conta multi-usuário com RLS, chat com múltiplos modelos, agenda com
proposta e aprovação, e planejamento de estudo que decompõe um assunto
em sessões, respeita pré-requisitos e recalibra as estimativas pelo
tempo real.

Validado: 22/22 + 34/34 + 27/27 + 30/30 nos smokes, 215 testes
unitários e de contrato. As tarefas chegam no M4.

**Falta a interface.** O backend está completo e testado; a UI tem só a
página de diagnóstico do M0.

---

## Rodar

Requisitos: Docker e Docker Compose. Mais nada — Python e Node vivem nos
containers.

**Windows (PowerShell)** — `make` não vem no Windows, então use o script:

```powershell
.\age.ps1 env     # gera .env com segredos aleatórios
.\age.ps1 up      # sobe os serviços e aplica as migrations
.\age.ps1 smoke   # valida a infraestrutura (M0)

# valida o produto, marco por marco
bash infra/smoke-m1.sh   # chat multi-modelo
bash infra/smoke-m2.sh   # agenda
bash infra/smoke-m3.sh   # estudo
```

**Linux / macOS:**

```bash
make env
make up
make smoke                 # infraestrutura
bash infra/smoke-m1.sh     # chat
bash infra/smoke-m2.sh     # agenda
bash infra/smoke-m3.sh     # estudo
```

Os dois expõem os mesmos comandos; `.\age.ps1 help` ou `make help` lista
todos.

Depois: <http://localhost:3000> (diagnóstico) e
<http://localhost:8000/docs> (API).

> `make env` gera uma `MASTER_KEY` aleatória. **Perdê-la significa perder
> todas as credenciais salvas** — elas são cifradas com uma chave
> derivada dela. Guarde o `.env` fora do repositório.

## Comandos

| O que faz | PowerShell | make |
|---|---|---|
| lista tudo | `.\age.ps1 help` | `make help` |
| segue os logs | `.\age.ps1 logs` | `make logs` |
| suíte completa | `.\age.ps1 test` | `make test` |
| lint + typecheck | `.\age.ps1 lint` | `make lint` |
| migrations | `.\age.ps1 migrate` | `make migrate` |
| OpenAPI → tipos TS | `.\age.ps1 gen-types` | `make gen-types` |
| psql | `.\age.ps1 shell-db` | `make shell-db` |
| APAGA os volumes | `.\age.ps1 reset` | `make reset` |

## Arquitetura em uma tela

Três serviços, separados por **perfil de execução** — não por entidade:

```
browser ──▶ web (Next.js)  ──▶ core-api (FastAPI)  ──fila──▶ ai-worker
            BFF, páginas        auth, planos,                LLM, sync
                                agenda, tarefas              (jobs longos)
                                      ▲                          │
                                      └────── Redis Stream ◀─────┘
                                              (eventos do turno)
```

- **`web`** — UI e um BFF que só repassa. Zero regra de negócio ([§2.4.1](ARQUITETURA.md)).
- **`core-api`** — requests de milissegundos. Única superfície pública.
- **`ai-worker`** — jobs de segundos a minutos. Não é exposto; recebe por fila.

Por que separados: uma chamada a LLM pode levar 40 s ou pendurar; um
`GET /api/plans` leva 15 ms. No mesmo processo, um turno travado consome
worker HTTP e degrada a UI inteira.

O navegador **nunca** fala com o `ai-worker`. O worker publica eventos num
Redis Stream e o `core-api` repassa como SSE — por isso reiniciar ou
escalar o worker não derruba conexão de usuário, e perder o Wi-Fi no meio
de um turno não perde o turno.

### Três portas substituíveis

| Porta | Default | Outros |
|---|---|---|
| `LLMProvider` | DeepSeek | OpenAI, Gemini, Anthropic, Ollama |
| `CalendarProvider` | **LocalCalendar** | Google, CalDAV, Outlook |
| `TaskProvider` | **LocalTasks** | Google Tasks, Todoist |

Os defaults são os nativos de propósito: dá para usar o produto sem
conectar conta externa nenhuma, e uma porta com só uma implementação
apodrece sem ninguém notar.

### Segurança, em duas linhas

Row-Level Security no Postgres, com as roles de runtime marcadas
`NOBYPASSRLS` e separadas de quem é dono das tabelas — sem isso a
política seria decorativa ([§4.2.1](ARQUITETURA.md)). Credenciais de API
cifradas com AES-256-GCM e nunca devolvidas pela API.

## Estrutura

```
services/
  web/          Next.js 15 — UI e BFF
  core-api/     FastAPI — auth, planos, agenda, tarefas
  ai-worker/    FastAPI + ARQ — LLM e sincronização
packages/
  age-contracts/  payloads de fila e eventos (compartilhado)
  age-types/      tipos TS gerados do OpenAPI
infra/
  docker-compose.yml, Dockerfiles, init-db.sql, smoke.sh
```
