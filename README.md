# Age

Assistente de agenda conversacional. Você conversa em linguagem natural e
ele lê, propõe e escreve na sua agenda real — quebrando um assunto de
estudo em sessões, encaixando nos horários livres e reorganizando o que
for flexível quando necessário.

Nada é escrito sem você aprovar.

- **Como subir e encerrar:** [COMO-RODAR.md](COMO-RODAR.md)
- **Especificação completa:** [ARQUITETURA.md](ARQUITETURA.md)
- **Acompanhamento:** [PROGRESSO.md](PROGRESSO.md)

**Estado atual: os sete marcos estão concluídos** (M0 a M6).

O que funciona, ponta a ponta, contra containers reais:

- **Conta multi-usuário** com RLS imposto pelo Postgres, credencial de
  LLM cifrada e sessões revogáveis.
- **Chat com múltiplos modelos**, streaming token a token, troca de
  provedor no meio da conversa sem perder o histórico.
- **Agenda** com busca determinística de horário, proposta validada,
  aprovação granular e desfazer.
- **Estudo**: decompõe um assunto em sessões, respeita pré-requisitos,
  distribui com revisão espaçada e recalibra as estimativas pelo tempo
  real.
- **Tarefas**, ligadas à agenda: responde "o que vence e não tem tempo
  reservado", e "encaixa" cria o par bloco + tarefa.
- **Rotinas** com hora marcada, aderência sem streak, relatório que
  procura padrão no que foi pulado, e reação passiva a mudança externa.
- **Privacidade**: export completo, três níveis de exclusão e os
  limites ditos na cara — o que o provedor de LLM retém não volta.
- **Busca na web**, com um botão de pesquisa aprofundada: ele entra nos
  sites, filtra o resultado ruim por score de relevância e mostra as
  fontes que usou, clicáveis e guardadas junto da mensagem.
- **Anexos**: imagem e PDF arrastados para o chat, lidos pelo modelo.
- **Edição direta** (§8.0): criar evento arrastando na grade, editar
  tarefa e evento sem passar pelo chat. O que *você* faz é decisão
  tomada, não proposta a aprovar.
- **Uso e custo** por dia e por mensagem, com preço fora de pico e
  acerto de cache contabilizados.
- **Interface**: onboarding que não deixa ninguém num chat morto,
  chat com streaming token a token, revisão de plano item por item,
  timeline com proposto hachurado, e as telas de tarefa, estudo, rotina,
  relatório e privacidade. Tema escuro e claro, tipografia Inter
  auto-hospedada, ícones próprios e contraste AA verificado.

Validado: **290 verificações de smoke** (22 + 35 + 27 + 30 + 38 + 56 +
82) contra containers reais, **686 testes** no backend (384 no core-api,
302 no ai-worker) e **377** no front. Lint limpo nos três serviços, `tsc --noEmit` e
`prettier --check` sem erros, `next build` passando.

Abra <http://localhost:3000>. As APIs ficam em
<http://localhost:8000/docs>.

### O que ainda não tem

**Os adaptadores do Google**, adiados de propósito, com a suíte de
contrato já escrita e esperando: `GoogleCalendar` e `GoogleTasks`.
Construir OAuth, webhook e sync antes de o produto provar que a porta
está certa seria investir na parte errada — e a degradação do provedor
pobre já é testada contra um adaptador que declara as mesmas limitações
do Google Tasks.

**Arrastar evento na timeline.** A prop existe no componente, mas
ligá-la a um `PATCH` criaria um caminho de escrita sem `PlanDiff` e
fora do desfazer. O certo é o drag gerar um plano de um item só, e isso
precisa de uma rota que ainda não existe.

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
bash infra/smoke-m4.sh   # tarefas
bash infra/smoke-m5.sh   # rotinas e relatório
bash infra/smoke-ui.sh   # interface (fala com o web, não com a API)
```

**Linux / macOS:**

```bash
make env
make up
make smoke                 # infraestrutura
bash infra/smoke-m1.sh     # chat
bash infra/smoke-m2.sh     # agenda
bash infra/smoke-m3.sh     # estudo
bash infra/smoke-m4.sh     # tarefas
bash infra/smoke-m5.sh     # rotinas
bash infra/smoke-ui.sh     # interface
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
