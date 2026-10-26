# Sete defeitos: o vínculo que não vincula, e as telas que não deixam editar

## Context

Você testou o Age e trouxe sete problemas. Antes de propor conserto eu
fui atrás de cada um no código e nos logs do seu sistema — e **seis dos
sete estão confirmados com evidência**, não com suposição.

### Os sete, como eu os entendi

1. **A mensagem digitada some** ao sair do chat e voltar.
2. **Os filmes da Pixar saíram errados**: você pediu os **3 primeiros**
   em 3 domingos; ele criou **4 eventos**, com a ordem trocada.
3. **Não vinculou os to-dos ao calendário sozinho** no estudo de
   funções em Python; você teve que pedir depois.
4. **O card de vínculo veio vazio** (sua imagem): o texto diz "preparei
   os 4 vínculos, todos aceitos", o card diz **"0 itens · 0 marcados"**
   e o botão vira "Aplicar os 0 itens".
5. **Não dá para criar evento à mão** no calendário — e você quer a tela
   bonita.
6. **A tela de reservar horário é ruim**: só lista horários prontos e
   não deixa digitar o seu.
7. **Não existe edição** de to-do nem de evento depois de criados.
8. **O Deep Search não mostra as fontes**: nenhuma referência visual de
   que pesquisou, nem em quais sites entrou.
9. **O rascunho tem que valer para todo chat**, com uma marca na lista
   dizendo que ali tem mensagem não enviada.

Os dois últimos vieram depois, junto com o veredito sobre os anexos:
**o menu de arquivo e PDF ficou bom, nada a mexer** — fica registrado
para eu não ficar remexendo no que já serve.

### A prova do #4, nos seus próprios dados

Os logs do `core-api` têm os seus quatro itens, com os seus títulos:

```
item 0 descartado: 'Definir, chamar e retorno de funções': nenhum dos blocos existe mais
item 1 descartado: 'Parâmetros posicionais, nomeados e padrão': nenhum dos blocos existe mais
item 2 descartado: 'Escopo e variáveis locais, erros comuns': nenhum dos blocos existe mais
item 3 descartado: '*args/**kwargs e lambda rápido': nenhum dos blocos existe mais
```

E, no turno anterior, a causa raiz:

```
item 3 descartado: evento c48de6af-… não existe na agenda
item 4 descartado: evento 0b518939-… não existe na agenda
item 5 descartado: evento efaa22f6-… não existe na agenda
```

**O mecanismo**, em `plan_store.py:259-275`: cada item que falha é
descartado com `log.info` + `continue`. O plano é criado assim mesmo,
vazio. Nada volta para o worker, então o modelo não tem como saber que
falhou — e descreve a intenção como se fosse o resultado. É exatamente
o padrão de [[ferramenta-que-nao-devolve-erro]].

**Por que os blocos "não existem"**: `task_store.resolve_events()`
consulta só `local_events` e `events_shadow` — eventos **aplicados**.
Os blocos que o Age acabou de propor vivem em `plan_items` e ainda não
foram aprovados, então não estão em lugar nenhum que essa query olhe.
É [[propostos-nao-ocupam-horario]] de novo, agora no vínculo.

### O #2 não é o que parecia — li as conversas

**Correção honesta:** eu tinha escrito aqui que ele errou a ordem dos
filmes. Li as duas conversas no banco e **não errou**. Em 25/10 ele diz,
com todas as letras:

> "os três primeiros da Pixar são Toy Story (1995), Vida de Inseto
> (1998) e Toy Story 2 (1999). Então falta justamente o primeiro, e o
> Monstros S.A. é o quarto."

O que de fato aconteceu, em duas conversas separadas por cinco dias:

- **20/10** — você pediu **os 4 primeiros**. Ele propôs os quatro na
  ordem certa: Toy Story 25/10, Vida de Inseto 01/11, Toy Story 2
  08/11, Monstros S.A. 15/11. **Só três foram aprovados**; o Toy Story
  de 25/10 ficou para trás.
- **25/10** — você pediu os **3 primeiros**. Ele olhou a agenda, viu
  que faltava o Toy Story, e propôs pôr no único domingo livre (aquele
  dia). **Você não respondeu** — mandou a pergunta de Python em
  seguida, e a proposta ficou pendente.

Então o calendário tem 4 filmes porque **você pediu 4** em 20/10, e a
ordem "deslocada" é a lista de 4 menos o primeiro item.

**O defeito real aqui é de interface, não de raciocínio:** a proposta
de 25/10 nunca foi respondida e sumiu da sua vista. Você olhou a
agenda, viu o resultado errado, e não tinha como saber que havia uma
correção esperando aprovação. Isso é o mesmo buraco dos defeitos 5 e 7
— **não dá para ver nem editar o que está lá**, então a única forma de
consertar é pedir ao chat de novo.

A regra de "pesquisar fato verificável" continua no plano, mas como
**prevenção**, não como conserto deste caso: ele acertou de memória
desta vez, e ordem de lançamento é exatamente o tipo de fato em que o
próximo erro passa despercebido.

### O #3, relido na conversa

Você pediu *"separe cada dia em atividades todo e bote no sistema"*, e
ele respondeu afirmando o vínculo:

> "4 atividades, 2h no total, duas por dia. **Cada uma já está amarrada
> ao bloco do dia dela**: as duas de segunda no bloco de 15h, as duas de
> terça no de 14h."

Ele **afirmou** o vínculo. Os logs mostram que os itens foram
descartados em silêncio no `plan_store` — então não é que ele se
recusou a fazer, é que **achou que fez**. Mesmo defeito do #4, um turno
antes.

A regra de `prompts.py:483` ("não chame `create_task` no mesmo turno em
que oferece") continua valendo a pena ajustar, porque na conversa de
antes ele de fato parou para perguntar — mas ela **não** é a causa
deste caso.

E as 10 tarefas de Funções em Python na sua lista mostram o acúmulo: as
6 antigas + as 4 novas, porque as 4 novas nunca ligaram aos blocos.

### O defeito que atravessa tudo: proposta pendente é invisível

Três dos sete casos têm a mesma raiz. Uma proposta que você não
respondeu **some da sua vista** mas continua existindo:

- o Toy Story de 25/10 ficou pendente e você nunca soube;
- os vínculos de Python foram descartados sem ninguém avisar;
- o card veio com "0 itens" e sem explicação do que faltou.

Em todos, a tela mostra um estado e a verdade é outra. Isso reforça as
Partes 1, 4 e 5: ver, editar e corrigir **sem passar pelo chat**.

### O #6 é decisão deliberada, não esquecimento

A docstring de `reserve-time-dialog.tsx:1-15` defende a ausência do
campo manual: *"quem digita horário à mão escolhe sem saber que vai
colidir com a aula das 16h"*. O argumento é legítimo — por isso o campo
manual entra **com aviso de conflito**, e não removendo a checagem.

---

## O que vai ser feito

### Parte 1 — O vínculo (defeitos 3 e 4)

**1.1 Descarte deixa de ser silencioso.** `plan_store.py` passa a
devolver os itens descartados junto do plano, com o motivo. O card
mostra: *"4 vínculos não entraram: os blocos ainda não estão na agenda"*.
Um plano que ficaria **vazio** não é criado — a rota responde erro, e o
chat diz o que houve em vez de mostrar "Aplicar os 0 itens".

**1.2 Bloco proposto passa a resolver.** `resolve_events()` ganha uma
terceira fonte: `ai.plan_items` do mesmo usuário, com plano ainda
`proposed` e não expirado. Um vínculo para bloco proposto é legítimo —
os dois serão aprovados no mesmo cartão.

**1.3 O par nasce junto.** Quando o Age propõe blocos E tarefas no
mesmo turno, o vínculo usa `cobre_eventos` (o caminho que já existe
para isso, na tabela de `prompts.py:492`), em vez de `link_task_to_event`
com id de bloco que ainda não existe.

### Parte 2 — A proposta pendente fica visível (defeito 2)

O conserto do caso dos filmes **não** é no prompt: é dar visibilidade
ao que está pendente. Um aviso persistente ("1 proposta esperando sua
resposta") com link para o cartão, em vez de ela sumir quando a
conversa segue. Reusa o que `plan-review.tsx` já renderiza.

**Prevenção, não conserto:** o prompt ganha a regra de pesquisar fato
externo verificável (ordem de lançamento, datas, elenco, preço) com
`web_search` antes de afirmar. Desta vez ele acertou de memória; da
próxima o erro passaria despercebido, porque ninguém confere a
filmografia da Pixar antes de confiar na agenda.

### Parte 2b — "Faça" não é "ofereça" (defeito 3)

A regra de `prompts.py:483` ganha exceção explícita: **pedido direto é
para fazer**. "Faz as atividades, separa nos dois dias" é ordem, não
convite a oferecer. A oferta continua valendo para o caso que a regra
mirava: quando o Age pensou nas tarefas sozinho.

### Parte 3 — O rascunho que some, e o aviso de pendente (defeitos 1 e 9)

**3.1 Guardar por conversa.** O texto do composer vai para
`localStorage` (não `sessionStorage`: você pediu que sobreviva a sair e
voltar de verdade), com chave por conversa — `age:rascunho:<id>` —,
salvo com debounce e apagado ao enviar. Cada chat guarda o seu, como
você pediu: "vai estar salvo para qualquer chat".

Leitura e escrita em `try/catch`: em aba anônima o acesso lança, e um
chat que quebra por causa de rascunho é pior que um rascunho perdido.

**3.2 A marca na lista de conversas.** Um ponto discreto na linha da
conversa que tem rascunho não enviado, em
`conversation-list/sidebar.tsx` (o componente `Row`, linha 479), com
`title`/`aria-label` dizendo "mensagem não enviada" — ponto colorido
sozinho não existe para quem não enxerga nem para quem não adivinha a
convenção.

A lista lê as chaves `age:rascunho:*` de uma vez, não uma leitura por
linha, e se atualiza quando o rascunho muda.

### Parte 3b — As fontes do Deep Search na tela (defeito 8)

Hoje não há **nenhuma** pista visual de que ele pesquisou. Medi por quê:

- `orchestrator.py:253` faz `ok, summary_text = True, None` — em caso de
  **sucesso o resumo é `None`**. Só erro produz texto. Por isso a sua
  caixa de "7 consultas" mostra o rótulo e mais nada.
- A saída da ferramenta (títulos, URLs, score, site) existe e é
  completa, mas vai **só para o modelo**, nunca para a tela.
- `MessageOut` (`conversations.py:107`) não tem campo de ferramenta, e o
  rastro vive só em memória: **um F5 apaga tudo**.

A caixa recolhível que você descreveu **já existe** — é o `ToolTrail`
de `messages.tsx:264`, o "7 consultas" do seu print, com seta que abre
e fecha. Falta conteúdo dentro dela.

**3b.1 A ferramenta devolve as fontes.** `web_search` passa a emitir,
junto do `tool_end`, a lista do que usou: título, URL, site e se era
confiável. `ToolEndEvent` tem `extra="forbid"`, então o campo entra no
contrato com default `None` — versão antiga continua válida.

**3b.2 O `ToolTrail` mostra fonte clicável.** Dentro da caixa que já
abre e fecha, cada busca lista as fontes como **links** para o site, no
mesmo padrão de segurança da resposta: só `http`/`https`,
`rel="noopener noreferrer"`. Fonte fraca (abaixo do limite de 1,5)
aparece **marcada como não verificada** — a classificação já existe no
backend e hoje morre ali.

**3b.3 Sobreviver ao F5.** Sem isso a lista some ao recarregar e você
fica de novo sem saber de onde veio. As fontes são gravadas junto da
mensagem e devolvidas em `MessageOut`, como os anexos já são.

Isso é o "micro-relatório clicável" que você descreveu: abre a caixa,
vê em que sites ele entrou, clica e vai para lá.

### Parte 4 — Editar (defeito 7)

Um diálogo de edição para tarefa e outro para evento, abertos por um
botão de lápis (o `IconEdit` já existe em `ui/icons.tsx` e **nenhum
componente usa**).

- **Tarefa** → `PATCH /tasks/{id}`, que já existe.
- **Evento** → precisa de rota nova. Aqui mora a decisão de arquitetura:
  §5.8 diz que nada entra na agenda sem aprovação, mas isso vale para o
  que o **modelo** propõe. Edição manual sua é ação direta, como o botão
  de reservar tempo já é. Então `PATCH /calendar/events/{id}` aplica na
  hora, só para `local_events` (evento vindo do Google não é nosso para
  editar), e some com confirmação quando há conflito.

### Parte 5 — Criar evento arrastando (defeito 5)

Clicar e arrastar na grade define o horário e abre o diálogo com data,
hora e duração preenchidas — só falta o título. Reusa o diálogo da
Parte 4, em modo de criação.

Três detalhes que decidem se funciona:

- **Arrastar tem de distinguir clique de arrasto**: um clique seco vira
  evento de duração mínima, não um bloco de 0 minutos.
- **Snap de 15 minutos**, senão sai "14h07 às 15h23".
- **Toque no celular**: `pointerdown/move/up`, não `mousedown`, senão o
  gesto não existe no telefone.

### Parte 6 — Reservar horário: refazer a tela (defeito 6)

O caminho exato que você descreveu: **aba Tarefas → `+` → reservar
tempo**. Hoje a tela pede a duração e devolve uma lista vertical de
horários sugeridos, e é só isso — nas suas palavras, "um menu de lista
de horas descendente" que não serve.

A tela é **refeita**, não remendada:

- **Duração** em botões rápidos (30 min · 1h · 1h30 · 2h) mais campo
  livre, em vez de só digitar minutos.
- **Data e hora na mão, em primeiro lugar** (sua escolha): seletor de
  data e campo de hora, abertos no próximo horário redondo. É o que
  você pediu — poder dizer *"quero nesse dia, nessa hora"*.
- **Sugestões embaixo, como atalho**, não como única via — e em cartões
  de linha com o dia e o motivo, não numa lista vertical comprida.
- **Aviso de conflito antes de confirmar**, dizendo com o quê colide.
  É o risco que a docstring de `reserve-time-dialog.tsx:1-15` usava
  para justificar não ter campo manual; agora ele é **tratado** em vez
  de evitado, o que é o que permite atender seu pedido sem reintroduzir
  o problema que aquela decisão protegia.

## Arquivos

| Onde | O quê |
|---|---|
| `services/core-api/app/services/plan_store.py` | descartes devolvidos, plano vazio recusado |
| `services/core-api/app/services/task_store.py` | `resolve_events` enxerga bloco proposto |
| `services/core-api/app/api/calendar.py` | `PATCH /calendar/events/{id}` |
| `services/ai-worker/app/prompts.py` | pedido direto é para fazer |
| `services/web/components/plan-review/plan-review.tsx` | mostrar o que não entrou |
| `services/web/components/chat/composer.tsx` | rascunho em `localStorage`, por conversa |
| `services/web/components/conversation-list/sidebar.tsx` | marca de rascunho pendente (`Row`, linha 479) |
| `packages/age-contracts/.../events.py` | fontes no `ToolEndEvent` (tem `extra="forbid"`) |
| `services/ai-worker/app/domain/orchestrator.py` | parar de mandar `summary=None` no sucesso |
| `services/ai-worker/app/domain/tools/web_tools.py` | devolver as fontes para a tela, não só ao modelo |
| `services/web/components/chat/messages.tsx` | fontes clicáveis no `ToolTrail` (linha 264) |
| `services/web/components/tasks/reserve-time-dialog.tsx` | hora manual + aviso de conflito |
| `services/web/components/calendar-timeline/` | arrastar na grade |
| `services/web/components/tasks/`, `calendar-screen.tsx` | diálogos de edição |

## Verificação

Cada teste com a sabotagem que **tem** de derrubá-lo, e confiro que a
sabotagem aplicou antes de confiar no verde
([[sabotagem-que-nao-aplica]], [[prettier-colapsa-ancora]]).

| Teste | Sabotagem que derruba |
|---|---|
| Vínculo para bloco **proposto** entra no plano | voltar `resolve_events` a só olhar evento aplicado — **o defeito da sua imagem** |
| Plano que ficaria vazio **não** é criado | voltar o `continue` mudo (produz "Aplicar os 0 itens") |
| Item descartado chega à tela com motivo | engolir o descarte no caminho de volta |
| Pedido direto cria a tarefa, não oferece | voltar a regra sem exceção |
| Fato verificável dispara `web_search` | tirar a regra (prevenção, não conserto) |
| Proposta não respondida continua visível | deixá-la sumir — foi assim que o Toy Story de 25/10 se perdeu |
| Rascunho sobrevive a sair e voltar | tirar o `localStorage` |
| Cada conversa guarda o SEU rascunho | usar uma chave só — o texto de um chat apareceria no outro |
| Rascunho em aba anônima não quebra o chat | tirar o `try/catch` |
| A linha da conversa marca rascunho pendente | esconder a marca; ou marcar todas |
| A marca tem nome acessível, não só cor | deixar só o ponto colorido |
| `web_search` devolve as fontes no `tool_end` | voltar `summary_text = None` no sucesso — **o estado de hoje** |
| As fontes viram link clicável no `ToolTrail` | renderizar como texto |
| Fonte fraca aparece marcada | mostrar igual à boa |
| As fontes sobrevivem ao F5 | não gravar na mensagem (some ao recarregar) |
| `PATCH` recusa evento do Google | aceitar qualquer `kind` |
| Clique seco não vira bloco de 0 min | tratar clique como arrasto |
| Hora manual com conflito avisa antes | confirmar direto |

**No navegador** ([[interface-precisa-ser-vista]],
[[playwright-ja-esta-na-maquina]]) — e isto não é opcional aqui, porque
**os sete defeitos são de tela e os 998 testes estavam verdes**:

1. Refazer o seu caso: criar os blocos de Python, pedir o vínculo, e ver
   **4 itens marcáveis** no card onde você viu 0.
2. Deixar uma proposta **sem responder**, mandar outra mensagem, e ver
   que o aviso de pendente continua na tela — é o que teria salvado o
   Toy Story de 25/10.
3. Escrever em **dois chats diferentes**, sair, voltar — cada um com o
   seu texto, e a marca de pendente nas duas linhas da lista.
4. **Perguntar algo que exija pesquisa**, abrir a caixa de consultas e
   clicar numa fonte; depois dar **F5** e conferir que elas continuam lá.
5. Arrastar na grade e criar um evento; editar; editar uma tarefa.
6. **Aba Tarefas → `+` → reservar tempo**, digitando uma hora que
   colide, e ver o aviso. É o caminho exato que você apontou.
7. 390px e 1440px, nos dois temas.

Lembrando [[porta-3000-e-o-prod]]: `localhost:3000` serve build
congelado. Rebuildar o `web-prod` e **conferir a string nova dentro do
bundle** antes de olhar a tela.

Fecha com `ruff`, `pytest`, `npm test`, `tsc`, `prettier`.

## Ordem, e o que eu faria primeiro

1. **Vínculo** (1.1 → 1.2 → 1.3) — é o defeito que quebra confiança: ele
   afirma ter feito e não fez.
2. **Fontes do Deep Search** — você acabou de usar a busca e ficou sem
   saber se funcionou; é o que mais muda a confiança por hora de
   trabalho, e a caixa recolhível já existe.
3. **Prompt + proposta pendente visível** — regras e um aviso.
4. **Rascunho** por conversa, com a marca na lista.
5. **Reservar tempo** — a tela que você mais xingou.
6. **Editar** tarefa e evento.
7. **Arrastar** na grade.

Dá para parar depois de qualquer um: cada passo fecha verde sozinho.

Subi "reservar tempo" (era o 6º) porque você o destacou duas vezes, e
pus as fontes em 2º porque é defeito de confiança: sem elas, a pesquisa
que entreguei hoje é indistinguível de o modelo inventar.

## Riscos que eu aceito, e por quê

**Vínculo para bloco proposto cria dependência entre dois planos.** Se
você aprovar a tarefa e rejeitar o bloco, o vínculo fica órfão. Trato
tratando os dois como par (é o que `linked_group` já faz), mas o caso
de aprovação parcial precisa de teste próprio — está na tabela.

**`PATCH` de evento fura a regra "nada sem aprovação" de propósito.**
Vale só para ação sua, direta, em evento local. Se você preferir que
toda edição passe por cartão de aprovação, é uma linha de mudança — me
diga e eu inverto.

## A conta de teste

Você disse que é conta de teste e que posso apagar tudo depois. Então,
**ao final**, limpo o estado dela (34 conversas, os eventos e as
tarefas acumuladas) para que a verificação rode num estado limpo e os
defeitos reapareçam ou não por mérito próprio, sem sobra de teste
anterior confundindo o resultado.

Apago **depois** de verificar, não antes: os dados atuais são a
evidência que eu usaria para conferir que o conserto funcionou.
