# Roteiro de teste

Escrito para você testar quando acordar. Leva uns 10 minutos.

Abra **http://localhost:3000** e dê `Ctrl+Shift+R` (o cache guarda a
versão antiga do JavaScript, e sem isso você testa o código de ontem).

Se os containers não estiverem de pé: `.\age.ps1 prod`

---

## 1. O cartão que não funcionava

Era o seu relato: *"apareceu o pop-up para selecionar, só que não
funcionou, deu algum erro"*.

Mande no chat:

```
marca dentista quinta 15h
```

**O que deve acontecer:** aparece o cartão de aprovação, e clicar em
**Aprovar** funciona. Antes, esse clique dava erro 404 em silêncio.
Você clicou 8 vezes.

Depois de aprovar, **uma faixa verde aparece com "Desfazer"**. Clique
nela e o evento sai da agenda. Esse botão nunca existiu antes.

## 2. Ajustar sem rejeitar tudo

Peça de novo o dentista e, no cartão, clique em **"Ajustar horário"**.
Mude a hora ou os minutos e salve. O item muda sem precisar rejeitar e
redigitar o pedido.

Ao lado tem **"Pedir mudança"**: um campo para escrever com suas
palavras, tipo *"prefiro de manhã"*. Enter envia.

## 3. O primeiro turno invisível

Era o outro relato: *"na primeira mensagem não aparece feedback, e só
vejo a resposta se der F5"*.

**Comece uma conversa nova** (botão "Nova conversa") e mande qualquer
coisa. Observe:

- deve aparecer **"Lendo seu pedido"**, depois **"Consultando sua
  agenda"**, depois **"Escrevendo a resposta"**;
- a resposta deve aparecer **sem precisar de F5**.

A partir da segunda mensagem já funcionava antes; o problema era só na
primeira.

## 4. Conversar antes de marcar

Este é o que mudou mais. Mande:

```
quero aprender machine learning, por onde começo?
```

**O que deve acontecer:** ele responde a pergunta e **não propõe nada
na agenda**. No fim, oferece encaixar e pergunta o ritmo com duas
opções concretas.

Antes, a mesma pergunta levava a 31 sessões e 24 horas propostas de
uma vez, sem nunca perguntar quanto tempo você tinha.

Aceite uma das opções e veja que aí sim ele monta.

## 5. Corrigir sem duplicar

Depois de ele propor algo, mande:

```
na verdade 16h, e só 30 min
```

**O que deve acontecer:** fica **um** cartão, no horário novo, e ele
diz que tirou o anterior. Antes ficavam dois, e era esse o mecanismo
que empilhou as 28 propostas de estudo na sua conta.

## 6. O cartão que não apareceu

Era o relato da conversa de Python: ele disse *"Preparei a proposta,
está esperando sua aprovação"* e **nenhum cartão apareceu**. Você só
viu as 3 sessões depois do F5.

Medi na sua conta: o rascunho ficou pronto às 19:22:57 e o plano só
virou visível às 19:24:35, quando você atualizou. **98 segundos
invisível.**

Peça qualquer coisa que gere proposta e confira que o cartão aparece
**sem F5**. Ele é a confirmação: não existe outra pergunta no chat.

## 7. Voltar onde você estava

O pedido. Faça nesta ordem:

1. abra uma conversa qualquer no chat;
2. vá para **Agenda** e troque para **mês**;
3. volte para **Conversa** pela navegação.

**O que deve acontecer:** volta para a conversa que estava aberta, não
para a tela vazia. E ao voltar para a Agenda, ela está em **mês**.

A **data** volta sempre para hoje, de propósito: foi o que você
escolheu quando perguntei. Guardar a data faria você cair numa semana
velha ao voltar dias depois.

## 8. Apagar conversas

Na lista à esquerda, **"Selecionar conversas"** abre caixas de seleção
com "Selecionar todas". O menu de três pontos de cada conversa agora
também pergunta antes de apagar.

---

## O que eu não consegui verificar

Não tenho navegador aqui. Validei tudo por teste de componente e
conferindo que o código chega ao JavaScript que o navegador recebe,
mas **não vi nenhuma dessas telas com os olhos**.

Se algo estiver feio, desalinhado ou confuso de usar, me manda que eu
ajusto. Esse tipo de problema é invisível para os testes que eu rodo.

---

## Estado

- 195 testes no front, 294 no core-api, 113 no worker
- `ruff` e `tsc` limpos
- Custo da madrugada: **US$ 0,38**
- Sua conta: 0 eventos, 1 proposta ("Limpar a casa") esperando você
