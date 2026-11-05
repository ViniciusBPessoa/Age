# Subir o Age no GitHub e trabalhar de outra máquina

Tutorial do zero: o projeto hoje existe **só neste PC**, em 92 commits.
Nada foi publicado ainda.

Ao final você vai ter o código no GitHub (privado), o CI rodando sozinho
a cada push, e o projeto funcionando em qualquer outro computador.

---

## Parte 1 — Publicar

### 1. Abra o GitHub Desktop

Ele já está com este projeto aberto. No topo deve dizer
**Current repository: Age**. Se não estiver, vá em
`File → Add local repository` e escolha a pasta
`C:\Users\Pichau\Desktop\Faculdade\Projects\Age`.

### 2. Clique em "Publish repository"

É o botão azul no topo. Se aparecer `Push origin` em vez dele, o
repositório já foi publicado — pule para a Parte 2.

### 3. Preencha assim

| Campo | O que pôr |
|---|---|
| **Name** | `age` (ou `Age`) |
| **Description** | Assistente de agenda conversacional |
| **Keep this code private** | ✅ **marcado** |
| **Organization** | None / seu usuário |

O nome vira a URL: `github.com/ViniciusBPessoa/age`. Pode mudar depois,
em Settings, sem perder nada.

> **A caixa "Keep this code private" tem que ficar marcada.** Se
> desmarcar, o código fica visível para qualquer pessoa na internet —
> inclusive a especificação inteira em `ARQUITETURA.md`.

### 4. Clique em "Publish repository"

Leva alguns segundos: são 1,2 MB.

---

## Parte 2 — O que acontece sozinho

### O CI começa a rodar

O projeto já tem `.github/workflows/ci.yml`, e ele dispara **no primeiro
push**. Em `github.com/SEU-USUARIO/age/actions` você vai ver quatro
jobs:

| Job | O que faz |
|---|---|
| `backend (core-api)` | `ruff` + `pytest` |
| `backend (ai-worker)` | `ruff` + `pytest` |
| `frontend` | typecheck, testes de componente, build |
| `integration` | sobe tudo no Docker, migrations, smoke |

**Você não precisa configurar segredo nenhum.** O job de integração
gera o próprio `.env` com senhas aleatórias (`openssl rand -hex 24`), e
o `.env` da sua máquina continua fora do git, como sempre esteve.

Se algum job ficar vermelho, não entre em pânico: é a primeira vez que
esse CI roda num Linux limpo. Me manda o log que eu vejo.

### O `.env` NÃO vai subir

Está no `.gitignore` desde o começo e nunca foi commitado — conferi o
histórico inteiro. Sua chave da DeepSeek fica só aqui.

---

## Parte 3 — Trabalhar de outra máquina

### Opção A — Outro computador seu

O caminho mais simples é instalar o **GitHub Desktop** na outra máquina,
entrar com sua conta e usar `File → Clone repository`, que já cuida do
login. Por linha de comando:

```bash
git clone https://github.com/SEU-USUARIO/age.git
cd age
```

Como o repositório é **privado**, o `git clone` vai pedir autenticação.
A senha da conta não funciona mais: use um Personal Access Token
(`github.com/settings/tokens`) no lugar da senha, ou o GitHub Desktop,
que resolve isso para você.

Depois, **o passo que todo mundo esquece**: o `.env` não vem no clone,
porque é segredo. Crie-o a partir do exemplo:

```bash
cp .env.example .env
```

E preencha os valores. São 9 variáveis; as de senha podem ser qualquer
coisa com 32+ caracteres, menos:

- **`MASTER_KEY`** — é a chave que cifra as credenciais de LLM no banco.
  Se você puser uma diferente da deste PC, o app sobe mas **não
  consegue descifrar as chaves já salvas**: a conta existe e o chat não
  responde. Para usar o mesmo banco, copie o valor daqui.
- **`PUBLIC_BASE_URL`** — `http://localhost:3000` serve.

Para gerar as outras, em qualquer terminal Linux/Mac:

```bash
openssl rand -hex 24
```

Depois:

```bash
make up        # Linux / Mac
.\age.ps1 up   # Windows
```

> **No Linux ou Mac use `make`, não `age.ps1`** — aquele script é
> PowerShell e só roda no Windows. O `Makefile` tem os mesmos alvos
> (`make up`, `make migrate`, `make smoke`).

### Opção B — GitHub Codespaces (navegador, sem instalar nada)

No repositório: botão verde **Code → Codespaces → Create codespace on
main**. Abre um VS Code no navegador, com Docker já disponível.

Ainda precisa do `.env`: mesmo passo da Opção A.

A conta gratuita inclui uma cota mensal de horas-núcleo (confira o valor
atual em `github.com/settings/billing`, porque a GitHub muda isso). O
que importa para este projeto: ele sobe **6 containers** — postgres,
redis, core-api, ai-worker, ai-worker-api e web — então peça uma máquina
de 4 núcleos, e lembre que a cota é consumida por núcleo-hora, não por
hora. Pare o codespace quando terminar.

### Opção C — Claude na web

Em claude.ai/code, conectando esta conta do GitHub, dá para pedir
alterações no repositório sem ter o projeto na máquina. Serve para
mexer no código; não roda o Docker.

---

## Parte 4 — Hábito que vale a pena

### Antes de trocar de máquina, dê push

O GitHub Desktop mostra `Push origin` com um número quando há commit
local não publicado. Se você mudar de computador sem publicar, o outro
clone não vê o trabalho — e aí é fácil editar o mesmo arquivo nos dois
lugares e criar conflito.

### Ao voltar, dê pull

`Fetch origin` → `Pull origin`. Primeiro ato do dia, antes de editar.

### Trabalhe em branch quando for mexer em algo grande

```bash
git switch -c nome-do-que-voce-vai-fazer
```

O CI roda em `pull_request` também, então você vê se quebrou **antes**
de juntar na `main`. No GitHub Desktop:
`Current branch → New branch`.

---

## O que este repositório já tem resolvido

- **Histórico limpo**: 92 commits, todos seus, sem rodapé de
  ferramenta.
- **CI completo**, que não depende de segredo configurado à mão.
- **`.gitattributes`**, para o final de linha não virar CRLF num clone
  Windows e quebrar os scripts de smoke no CI Linux.
- **`.env` e `.claude/` ignorados**, com os placeholders do
  `.env.example` sendo "troque-me", não valores reais.

## O que ainda não tem

- **Licença.** Em repositório privado não importa. Se um dia abrir,
  escolha uma em `github.com/SEU-USUARIO/age` → `Add file` →
  `Create new file` → nome `LICENSE` → o GitHub oferece um seletor.
- **Branch protection.** Para projeto de uma pessoa é burocracia; se um
  dia alguém mais commitar, vale exigir que o CI passe antes do merge.
