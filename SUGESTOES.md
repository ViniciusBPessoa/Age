# Sugestões para o Age

Escritas depois de ler a especificação inteira (`ARQUITETURA.md`), o
`PROGRESSO.md` e o código, e de usar o sistema com uma semana de
exemplo. A ideia central do Age, como a entendo:

> Um assistente que **lê a agenda real**, transforma "preciso estudar X
> até dia 20" em sessões concretas nos horários livres, e **nunca
> escreve sem aprovação**. O público é quem estuda e tem a semana
> fragmentada entre aulas, trabalho e vida.

As sugestões abaixo reforçam essa ideia e respeitam os não-objetivos de
§1.2: sem mensagem por conta própria, sem colaboração e sem app nativo.
Quando uma sugestão esbarra num deles, ela está marcada como
**decisão sua**.

Esforço: **P** (até 1 dia), **M** (2 a 5 dias), **G** (1 a 2 semanas).

## Prioridade alta

| # | Sugestão | Por quê | Esforço |
|---|---|---|---|
| 1 | **Criptografia dos dados sensíveis em repouso** | Já combinada como próxima etapa. Títulos, descrições e conversas são o que há de mais pessoal no banco. A infraestrutura de chave por usuário já existe (`core/crypto.py`, HKDF a partir da `MASTER_KEY`). | M |
| 2 | **Importar a grade da faculdade por foto ou PDF** | O primeiro uso de quem chega é cadastrar as aulas, e isso é chato. O chat já lê imagem e PDF: falta uma intenção dedicada que devolva eventos **fixos e recorrentes** como proposta. É o momento "uau" do onboarding. | M |
| 3 | **Importar e exportar `.ics`** (iCalendar) | Importar o calendário acadêmico ou o de outro app sem esperar os adaptadores do Google. Exportar um link somente leitura deixa a agenda do Age aparecer no celular, no app de calendário nativo. Resolve "não há app nativo" sem fazer um. | M |
| 4 | **Adaptadores Google Calendar e Google Tasks** | Já planejados, com a suíte de contrato escrita e esperando (`README`, "O que ainda não tem"). Quem usa o Google não vai largar o Google; o Age precisa ler de lá. | G |
| 5 | **Modo foco ligado ao bloco da agenda** | Um cronômetro (estilo Pomodoro) que abre a partir de uma sessão de estudo e registra o tempo **real** ao terminar. O Age já recalibra estimativas pelo tempo real (§5.5); hoje esse dado depende de a pessoa contar no chat. | M |

## Prioridade média

| # | Sugestão | Por quê | Esforço |
|---|---|---|---|
| 6 | **Paleta de comandos (Ctrl+K)** | Buscar evento, tarefa ou conversa e executar ações ("nova tarefa", "ir para sexta") de qualquer tela. É o padrão do Linear, do Notion e do Notion Calendar, e combina com os atalhos que a agenda ganhou. | M |
| 7 | **Linguagem natural na criação rápida, sem LLM** | Escrever "dentista qui 15h" no balão da agenda e o horário se ajustar sozinho, com um parser local de datas em pt-BR. Rápido, grátis e funciona sem chave de API. | P |
| 8 | **Mapa de carga no mês** | A rota `GET /calendar/workload` já existe e não aparece em nenhuma tela. Pintar os dias do mês (e do mês em miniatura) pela carga mostra a semana sobrecarregada antes de marcar mais. | P |
| 9 | **Provas como entidade** | Hoje a prova é só a data-limite de um plano. Tratá-la como coisa própria (matéria, data, peso) dá uma contagem regressiva na agenda e deixa o Age priorizar entre duas provas na mesma semana. | M |
| 10 | **Revisão semanal guiada** | Um botão "Planejar a semana" que abre uma conversa com roteiro: o que ficou para trás, o que vence, proposta para a semana. Respeita §1.2, porque é a pessoa que inicia. | P |
| 11 | **PWA instalável** | Manifesto e ícone para "Adicionar à tela inicial", com abertura em tela cheia no celular. Leitura offline da agenda da semana é o passo seguinte. | P |
| 12 | **Passkey ou 2FA (TOTP)** | O login já é sólido (Argon2id, refresh rotativo, limite por IP agora corrigido). O próximo degrau de segurança da conta é um segundo fator. | M |

## Pedem decisão sua

| # | Sugestão | O conflito |
|---|---|---|
| 13 | **Lembrete opcional antes de um compromisso** (notificação do navegador, só se a pessoa ligar) | §1.2 diz "sem notificação push". Um lembrete pedido pela própria pessoa, só para eventos, é diferente de cobrança ("você pulou a academia"). Vale reabrir? |
| 14 | **Link público de horários livres** (só livre/ocupado, sem títulos) | §1.2 exclui colaboração. Isto não compartilha a agenda; serve para marcar reunião de grupo de TCC sem trocar dez mensagens. |

## Qualidade e operação

- **Teste de acessibilidade automático na CI** (axe-core com Playwright
  nas telas principais). O contraste AA hoje é garantido por cálculo e
  revisão; um teste impede a regressão.
- **Backup automático do Postgres** com restauração testada. É o único
  dado do produto que não se recupera.
- **Rastreamento de erros no front** (Sentry ou OpenTelemetry). O
  `trace_id` já atravessa os três serviços; falta o navegador.
- **CSP com *nonce*** para tirar o `'unsafe-inline'` de script
  (`SEGURANCA.md`, próximos passos).
- **README desatualizado em dois pontos**: "Arrastar evento na
  timeline" já existe (esta branch), e a frase "O Age não tem chave
  própria" contradiz a chave de instância opcional (§3451).

## Velocidade das rotas (medida nesta revisão)

Build de produção, servidor local, mediana de 3 cargas a frio por rota,
navegador real (Chromium):

| Rota | Primeiro byte | Primeira pintura | JS da página | Chamadas de API |
|---|---:|---:|---:|---|
| `/login` | 10 ms | 112 ms | 127 KB | nenhuma |
| `/chat` | 88 ms | 228 ms | 155 KB | 3, até 65 ms |
| `/calendar` | 21 ms | 100 ms | 226 KB | 5, até 58 ms |
| `/tasks` | 19 ms | 112 ms | 148 KB | 3, até 36 ms |
| `/study` | 19 ms | 88 ms | 139 KB | 2, até 15 ms |
| `/routines` | 18 ms | 108 ms | 139 KB | 1, 11 ms |
| `/report` | 18 ms | 88 ms | 139 KB | 3, até 15 ms |
| `/settings` | 20 ms | 116 ms | 143 KB | 7, até 82 ms |
| `/settings/privacy` | 19 ms | 92 ms | 139 KB | 3, até 14 ms |

Todas abrem bem abaixo do limite de 1 s em que a espera começa a ser
notada. O `/chat` é o mais lento no primeiro byte porque busca dados no
servidor antes de responder; ainda assim, 88 ms. A agenda carrega mais
JavaScript (a biblioteca da grade com o arrastar). Se isso pesar no
celular, a barra lateral e os balões podem ser carregados sob demanda.

**Ponto de atenção:** essas medidas são do modo de produção
(`.\age.ps1 prod`). O modo de desenvolvimento (`up`) recompila cada tela
na primeira visita e leva segundos; não é a velocidade real do app.

**Defeito encontrado ao medir:** depois de 15 minutos parado, abrir
qualquer tela mandava para o login, mesmo com a sessão de 30 dias
válida. A renovação só acontecia nas chamadas de API, não no
carregamento da página. Corrigido nesta branch (`middleware.ts`).
