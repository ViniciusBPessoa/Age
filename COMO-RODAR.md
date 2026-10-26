# Como subir e encerrar o Age

Tutorial de terminal, do zero ao app aberto e de volta ao desligado.
Tudo roda em Docker: você não precisa de Python nem de Node instalados.

Abra o **PowerShell** na raiz do projeto:

```powershell
cd C:\Users\Pichau\Desktop\Faculdade\Projects\Age
```

Todos os comandos abaixo partem daí. O `.\` na frente é obrigatório no
PowerShell — sem ele o Windows não executa script da pasta atual.

---

## Subir

### Para usar e testar (recomendado)

```powershell
.\age.ps1 prod
```

Abre em **http://localhost:3000**. Esse é o modo que você quer no dia a
dia: serve o front já compilado, com 516 KB de JavaScript em vez dos
9,3 MB do modo de desenvolvimento. Foi o que resolveu a demora de 3 a 4
segundos para abrir cada tela.

O primeiro `prod` depois de mexer no código leva ~1 minuto, porque
recompila o front. Depois é rápido.

### Para mexer no código

```powershell
.\age.ps1 up
```

Abre em **http://localhost:3000** com hot-reload: salvar um arquivo
recarrega a tela. Mais lento para navegar, mas é o modo em que uma
alteração aparece sem rebuild.

> Rodando `prod`, o modo de desenvolvimento continua disponível em
> **http://localhost:3001**.

### Da primeira vez numa máquina nova

Só uma vez, antes do primeiro `up`:

```powershell
.\age.ps1 env
```

Gera o `.env` com segredos aleatórios. **Se você já tem `.env`, não rode
isto** — ele avisa e para, mas o aviso existe por um motivo: perder a
`MASTER_KEY` significa perder as credenciais salvas (sua chave da
DeepSeek, entre elas).

---

## Conferir se está no ar

```powershell
.\age.ps1 ps
```

Com tudo de pé você vê 6 serviços em `running`:

| serviço | o que é |
|---|---|
| `web-prod` (ou `web`) | a interface, na porta 3000 |
| `core-api` | a API, na 8000 (docs em http://localhost:8000/docs) |
| `ai-worker` | quem conversa com o modelo |
| `ai-worker-api` | as rotas internas do worker |
| `postgres` | o banco, na 5432 |
| `redis` | fila e cache |

Algo estranho na tela? Veja o que os serviços estão dizendo:

```powershell
.\age.ps1 logs
```

Sai com `Ctrl+C` — isso para de **acompanhar** os logs, não para os
serviços.

---

## Encerrar

```powershell
.\age.ps1 down
```

Para todos os containers e **mantém seus dados**: tarefas, eventos,
conversas e a chave da DeepSeek continuam lá. No próximo `up` ou `prod`
tudo volta como estava.

É este o comando para o fim do dia.

### Encerrar também o Docker

`down` libera a RAM dos containers, mas o Docker Desktop continua
rodando. Para fechar ele junto: ícone da baleia na bandeja do Windows →
*Quit Docker Desktop*.

---

## Apagar tudo e recomeçar

```powershell
.\age.ps1 reset
```

**Apaga o banco e o Redis.** Perde tarefas, eventos, conversas e as
credenciais salvas. Ele pede confirmação (`y`) antes. Depois, suba de
novo com `.\age.ps1 up`.

Use só quando quiser um ambiente limpo de propósito.

---

## Quando algo não sobe

| Sintoma | O que fazer |
|---|---|
| `.env não existe` | `.\age.ps1 env` |
| "Build falhou (provavelmente rede)" | Ele sobe com as imagens já construídas. Para reconstruir de verdade depois: `.\age.ps1 rebuild` |
| Porta 3000 ocupada depois de um `prod` | `.\age.ps1 down` (ele já remove o `web-prod` órfão) e suba de novo |
| Mudei o código e a tela não mudou | No modo `prod` o front é um build: `.\age.ps1 prod` de novo. No modo `up`, reinicie o container do web |
| `core-api não ficou pronto` | `.\age.ps1 logs` e olhe as linhas do `core-api` |

---

## A lista inteira

```powershell
.\age.ps1 help
```

Além dos de cima, tem `test` (suíte completa), `lint`, `fmt`, `migrate`,
`smoke` (valida o ambiente), `shell-db` (abre o psql) e `gen-types`.

No Linux ou no Mac os mesmos comandos existem como `make up`, `make down`
e assim por diante — o `Makefile` é a referência, e o `age.ps1` existe
porque o Windows não traz `make`.
