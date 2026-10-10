#!/usr/bin/env bash
# Age — validação do M1.
#
# Critério de pronto (PROGRESSO.md): você cria conta, cola uma chave,
# conversa, troca de modelo no meio e o histórico sobrevive.
#
# Usa o provedor `mock` para não gastar token nem exigir chave real. O
# formato dos eventos é o mesmo de um provedor de verdade, então o que
# passa aqui passa com DeepSeek.
set -uo pipefail

API="http://localhost:8000/api/v1"
PASS=0
FAIL=0
EMAIL="smoke-m1-$RANDOM@exemplo.com"
SENHA="senha-de-smoke-123456"

ok()    { printf '  \033[32m✓\033[0m %s\n' "$1"; PASS=$((PASS+1)); }
bad()   { printf '  \033[31m✗\033[0m %s\n' "$1"; FAIL=$((FAIL+1)); }
head_() { printf '\n\033[1m%s\033[0m\n' "$1"; }

jget() { python -c "import json,sys; d=json.load(sys.stdin); print(d$1)" 2>/dev/null; }

# ---------------------------------------------------------------
head_ "1. Registro e sessão (§4.1)"

REG=$(curl -s -X POST "$API/auth/register" \
  -H 'Content-Type: application/json' \
  -c /tmp/age-m1-cookies.txt \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$SENHA\",\"name\":\"Smoke\"}")

TOKEN=$(echo "$REG" | jget "['access_token']")
if [ -n "$TOKEN" ]; then
  ok "conta criada e já autenticada"
else
  bad "registro falhou: $(echo "$REG" | head -c 150)"
  printf '\n\033[31mM1 incompleto.\033[0m\n\n'; exit 1
fi

AUTH=(-H "Authorization: Bearer $TOKEN")

if grep -q "age_refresh" /tmp/age-m1-cookies.txt; then
  ok "cookie de refresh httpOnly recebido"
else
  bad "cookie de refresh ausente"
fi

ME=$(curl -s "$API/auth/me" "${AUTH[@]}")
if [ "$(echo "$ME" | jget "['email']")" = "$EMAIL" ]; then
  ok "/auth/me devolve o perfil"
else
  bad "/auth/me falhou"
fi

# Senha errada não deve passar, e a mensagem não distingue de e-mail
# inexistente (evita enumerar contas).
WRONG=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"senha-errada-aqui\"}")
if [ "$WRONG" = "401" ]; then
  ok "senha errada é recusada (401)"
else
  bad "senha errada retornou $WRONG"
fi

NOAUTH=$(curl -s -o /dev/null -w "%{http_code}" "$API/conversations")
if [ "$NOAUTH" = "401" ]; then
  ok "rota protegida exige token"
else
  bad "rota sem token retornou $NOAUTH"
fi

# ---------------------------------------------------------------
head_ "2. Credencial de LLM (§4.3)"

PROVIDERS=$(curl -s "$API/llm/providers" "${AUTH[@]}")
for p in deepseek openai mock; do
  if echo "$PROVIDERS" | grep -q "\"$p\""; then
    ok "provedor '$p' no catálogo do registry"
  else
    bad "provedor '$p' ausente"
  fi
done

CRED=$(curl -s -X POST "$API/llm/credentials" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d '{"provider_key":"mock","api_key":"sk-smoke-test-9876","default_model":"mock-chat"}')
CRED_ID=$(echo "$CRED" | jget "['id']")

if [ -n "$CRED_ID" ]; then
  ok "credencial salva"
else
  bad "falha ao salvar credencial: $(echo "$CRED" | head -c 150)"
fi

# A regra central de §4.3: a chave nunca volta pela API.
if echo "$CRED" | grep -q "sk-smoke-test-9876"; then
  bad "A CHAVE VAZOU na resposta da API"
else
  ok "chave não é devolvida (só os últimos 4)"
fi

if [ "$(echo "$CRED" | jget "['key_last4']")" = "9876" ]; then
  ok "últimos 4 dígitos expostos para reconhecimento"
else
  bad "key_last4 incorreto"
fi

LIST=$(curl -s "$API/llm/credentials" "${AUTH[@]}")
if echo "$LIST" | grep -q "sk-smoke"; then
  bad "A CHAVE VAZOU na listagem"
else
  ok "listagem também não expõe a chave"
fi

VERIFY=$(curl -s -X POST "$API/llm/credentials/$CRED_ID/verify" "${AUTH[@]}")
if [ "$(echo "$VERIFY" | jget "['ok']")" = "True" ]; then
  ok "verificação contra o provedor funciona"
else
  bad "verificação falhou: $(echo "$VERIFY" | head -c 150)"
fi

# ---------------------------------------------------------------
head_ "3. Conversa e streaming (§6.2, §7.1)"

CONV=$(curl -s -X POST "$API/conversations" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d '{"provider_key":"mock","model":"mock-chat"}')
CV=$(echo "$CONV" | jget "['id']")

if [ -n "$CV" ]; then
  ok "conversa criada"
else
  bad "falha ao criar conversa"
  printf '\n\033[31mM1 incompleto.\033[0m\n\n'; exit 1
fi

TURN=$(curl -s -X POST "$API/conversations/$CV/messages" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d '{"content":"preciso estudar algebra linear"}')
TID=$(echo "$TURN" | jget "['turn_id']")

if [ -n "$TID" ]; then
  ok "mensagem aceita com 202 (não espera o modelo)"
else
  bad "envio falhou: $(echo "$TURN" | head -c 150)"
fi

STREAM=$(curl -sN --max-time 15 "$API/turns/$TID/stream" "${AUTH[@]}" 2>/dev/null \
  | head -c 3000 || true)

echo "$STREAM" | grep -q 'event: token' \
  && ok "token chegou por SSE" \
  || bad "nenhum token no stream"

echo "$STREAM" | grep -q 'event: done' \
  && ok "turno completou (done)" \
  || bad "turno não completou"

sleep 3

MSGS=$(curl -s "$API/conversations/$CV/messages" "${AUTH[@]}")
COUNT=$(echo "$MSGS" | python -c "import json,sys; print(len(json.load(sys.stdin)['items']))" 2>/dev/null || echo 0)

if [ "$COUNT" -ge 2 ]; then
  ok "pergunta e resposta persistidas ($COUNT mensagens)"
else
  bad "persistência falhou (só $COUNT mensagens)"
fi

if echo "$MSGS" | grep -q '"role":"assistant"'; then
  ok "resposta do modelo drenada de ai -> core (§2.3.2)"
else
  bad "resposta do modelo não chegou a core.messages"
fi

# Conversa SEPARADA para o título: tanto `GET /conversations/{id}`
# quanto `GET .../messages` aplicam a sugestão como efeito colateral, e
# a conversa `$CV` já passou pelos dois acima. Checar nela mediria o
# efeito colateral, não a listagem.
#
# O caso real é quem manda a primeira mensagem e FICA na tela: a
# sidebar lê a lista e mostrava "Sem título" até recarregar.
CVT=$(curl -s -X POST "$API/conversations" "${AUTH[@]}" \
  -H 'Content-Type: application/json' -d '{}' | jget "['id']")
curl -s -o /dev/null -X POST "$API/conversations/$CVT/messages" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d '{"content":"organizar a semana de provas"}'

# O job de título tem `_defer_by=3`; espera por ele sem fixar um sleep.
LIST_TITLE=""
for _ in $(seq 1 12); do
  sleep 2
  LIST_TITLE=$(curl -s "$API/conversations" "${AUTH[@]}" \
    | python -c "import json,sys; d=json.load(sys.stdin); print(next((c['title'] or '' for c in d['items'] if c['id']=='$CVT'), ''))" 2>/dev/null)
  [ -n "$LIST_TITLE" ] && [ "$LIST_TITLE" != "None" ] && break
done

if [ -n "$LIST_TITLE" ] && [ "$LIST_TITLE" != "None" ]; then
  ok "título aparece na listagem, sem abrir a conversa"
else
  bad "título só materializa ao abrir a conversa (sidebar fica vazia)"
fi

TITLE=$(curl -s "$API/conversations/$CV" "${AUTH[@]}" | jget "['title']")
if [ -n "$TITLE" ] && [ "$TITLE" != "None" ]; then
  ok "título gerado automaticamente (§6.2.2)"
else
  bad "título não foi gerado"
fi

# ---------------------------------------------------------------
head_ "4. Troca de modelo no meio da conversa (§3.1.3)"

# O requisito central do M1: "começo no DeepSeek, depois vou pro Gemini".
# `mock-no-tools` tem capacidades diferentes (sem tools, sem role
# system, janela menor), então exercita `normalize_for` de verdade.
SWITCH=$(curl -s -X PATCH "$API/conversations/$CV" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d '{"model":"mock-no-tools"}')

if [ "$(echo "$SWITCH" | jget "['model']")" = "mock-no-tools" ]; then
  ok "modelo trocado na conversa existente"
else
  bad "troca de modelo falhou"
fi

TURN2=$(curl -s -X POST "$API/conversations/$CV/messages" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d '{"content":"e agora, com outro modelo?"}')
TID2=$(echo "$TURN2" | jget "['turn_id']")

sleep 6
STATE=$(curl -s "$API/turns/$TID2" "${AUTH[@]}")

if [ "$(echo "$STATE" | jget "['status']")" = "done" ]; then
  ok "turno com modelo novo completou SEM erro de formato"
else
  bad "turno falhou após troca: $(echo "$STATE" | head -c 200)"
fi

if [ "$(echo "$STATE" | jget "['model']")" = "mock-no-tools" ]; then
  ok "turno registrou o modelo correto"
else
  bad "modelo do turno divergente"
fi

MSGS2=$(curl -s "$API/conversations/$CV/messages" "${AUTH[@]}")
if echo "$MSGS2" | grep -q "mock-chat" && echo "$MSGS2" | grep -q "mock-no-tools"; then
  ok "histórico guarda qual modelo gerou cada mensagem"
else
  bad "rastreamento de modelo por mensagem falhou"
fi

if echo "$MSGS2" | grep -q "algebra linear"; then
  ok "histórico anterior sobreviveu à troca"
else
  bad "HISTÓRICO PERDIDO na troca de modelo"
fi

# ---------------------------------------------------------------
head_ "5. Idempotência (§5.1)"

# Id novo por execucao: um id fixo persiste entre rodadas e a guarda
# de idempotencia o rejeitaria (corretamente), fazendo o teste medir
# a coisa errada.
FIXED_TURN=$(python -c "import uuid; print(uuid.uuid4())")
# A idempotencia e observavel na RESPOSTA: dois POSTs com o mesmo
# client_turn_id devem devolver o MESMO message_id.
#
# Antes este teste contava "duplo clique" no texto das mensagens, o que
# passou a falhar quando o Mock comecou a chamar ferramentas em vez de
# ecoar o pedido — media o Mock, nao a idempotencia.
FIRST_ID=$(curl -s -X POST "$API/conversations/$CV/messages" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d "{\"content\":\"duplo clique\",\"client_turn_id\":\"$FIXED_TURN\"}" \
  | jget "['message_id']")

SECOND_ID=$(curl -s -X POST "$API/conversations/$CV/messages" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d "{\"content\":\"duplo clique\",\"client_turn_id\":\"$FIXED_TURN\"}" \
  | jget "['message_id']")

if [ -n "$FIRST_ID" ] && [ "$FIRST_ID" = "$SECOND_ID" ]; then
  ok "duplo envio com mesmo id devolve a mesma mensagem"
else
  bad "idempotencia falhou: '$FIRST_ID' != '$SECOND_ID'"
fi

# ---------------------------------------------------------------
head_ "6. Multi-chat (§6.2)"

CONV2=$(curl -s -X POST "$API/conversations" "${AUTH[@]}" \
  -H 'Content-Type: application/json' -d '{"title":"Segunda conversa"}')
CV2=$(echo "$CONV2" | jget "['id']")

TOTAL=$(curl -s "$API/conversations" "${AUTH[@]}" \
  | python -c "import json,sys; print(len(json.load(sys.stdin)['items']))" 2>/dev/null || echo 0)
if [ "$TOTAL" -ge 2 ]; then
  ok "múltiplas conversas por usuário ($TOTAL)"
else
  bad "multi-chat falhou (só $TOTAL)"
fi

curl -s -o /dev/null -X POST "$API/conversations/$CV2/pin" "${AUTH[@]}"
curl -s -o /dev/null -X POST "$API/conversations/$CV2/archive" "${AUTH[@]}"
ARCHIVED=$(curl -s "$API/conversations?filter=archived" "${AUTH[@]}" \
  | python -c "import json,sys; print(len(json.load(sys.stdin)['items']))" 2>/dev/null || echo 0)
if [ "$ARCHIVED" -ge 1 ]; then
  ok "arquivar funciona e tem filtro próprio"
else
  bad "arquivamento falhou"
fi

# Soft delete: sai da lista ativa mas vai para a lixeira (§6.2.4).
curl -s -o /dev/null -X DELETE "$API/conversations/$CV2" "${AUTH[@]}"
TRASH=$(curl -s "$API/conversations?filter=trash" "${AUTH[@]}" \
  | python -c "import json,sys; print(len(json.load(sys.stdin)['items']))" 2>/dev/null || echo 0)
if [ "$TRASH" -ge 1 ]; then
  ok "apagar é soft delete, com lixeira de 30 dias"
else
  bad "soft delete falhou"
fi

curl -s -o /dev/null -X POST "$API/conversations/$CV2/restore" "${AUTH[@]}"
RESTORED=$(curl -s -o /dev/null -w "%{http_code}" "$API/conversations/$CV2" "${AUTH[@]}")
if [ "$RESTORED" = "200" ]; then
  ok "restaurar da lixeira funciona"
else
  bad "restauração falhou ($RESTORED)"
fi

FORK=$(curl -s -X POST "$API/conversations/$CV/fork" "${AUTH[@]}")
if [ -n "$(echo "$FORK" | jget "['id']")" ]; then
  ok "ramificar conversa funciona (§8.1)"
else
  bad "fork falhou"
fi

# ---------------------------------------------------------------
head_ "7. Isolamento entre contas (§4.2)"

OTHER=$(curl -s -X POST "$API/auth/register" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"outro-$RANDOM@exemplo.com\",\"password\":\"$SENHA\"}")
OTHER_TOKEN=$(echo "$OTHER" | jget "['access_token']")

# A conversa do primeiro usuário não deve existir para o segundo.
PEEK=$(curl -s -o /dev/null -w "%{http_code}" "$API/conversations/$CV" \
  -H "Authorization: Bearer $OTHER_TOKEN")
if [ "$PEEK" = "404" ]; then
  ok "outro usuário não acessa conversa alheia (404, não 403)"
else
  bad "VAZAMENTO ENTRE CONTAS: retornou $PEEK"
fi

OTHER_LIST=$(curl -s "$API/conversations" -H "Authorization: Bearer $OTHER_TOKEN" \
  | python -c "import json,sys; print(len(json.load(sys.stdin)['items']))" 2>/dev/null || echo -1)
if [ "$OTHER_LIST" = "0" ]; then
  ok "conta nova vê zero conversas"
else
  bad "conta nova viu $OTHER_LIST conversas"
fi

OTHER_CREDS=$(curl -s "$API/llm/credentials" -H "Authorization: Bearer $OTHER_TOKEN" \
  | python -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo -1)
if [ "$OTHER_CREDS" = "0" ]; then
  ok "credenciais não vazam entre contas"
else
  bad "conta nova viu $OTHER_CREDS credenciais"
fi

# ---------------------------------------------------------------
head_ "Resultado"
printf '  %d passaram, %d falharam\n\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] && printf '\033[32mM1 validado.\033[0m\n\n' && exit 0
printf '\033[31mM1 incompleto.\033[0m\n\n' && exit 1
