#!/usr/bin/env bash
# Age — validação do M2 (agenda).
#
# Critério de pronto (PROGRESSO.md): "marca academia seg/qua/sex 19h"
# vira proposta, você aprova, e aparece na agenda.
#
# Testa o ciclo inteiro: calendário nativo → busca determinística de
# slots → proposta → aprovação granular → aplicação → undo.
set -uo pipefail

API="http://localhost:8000/api/v1"
PASS=0
FAIL=0
EMAIL="smoke-m2-$RANDOM@exemplo.com"
SENHA="senha-de-smoke-123456"

ok()    { printf '  \033[32m✓\033[0m %s\n' "$1"; PASS=$((PASS+1)); }
bad()   { printf '  \033[31m✗\033[0m %s\n' "$1"; FAIL=$((FAIL+1)); }
head_() { printf '\n\033[1m%s\033[0m\n' "$1"; }

jq_() { python -c "import json,sys; d=json.load(sys.stdin); print($1)" 2>/dev/null; }

COMPOSE="docker compose -f infra/docker-compose.yml --env-file .env"
psql_() { $COMPOSE exec -T postgres psql -U age_super -d age -tAc "$1" 2>&1; }

# ---------------------------------------------------------------
head_ "1. Conta e calendário nativo (§3.2.1)"

TOKEN=$(curl -s -X POST "$API/auth/register" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$SENHA\"}" | jq_ "d['access_token']")

if [ -z "$TOKEN" ]; then
  bad "registro falhou"
  printf '\n\033[31mM2 incompleto.\033[0m\n\n'; exit 1
fi
AUTH=(-H "Authorization: Bearer $TOKEN")
ok "conta criada"

# O nativo é o default: dá para usar a agenda sem conectar nada (§3.2.1).
CALS=$(curl -s "$API/calendar/calendars" "${AUTH[@]}")
CAL_ID=$(echo "$CALS" | jq_ "d[0]['id']")
if [ -n "$CAL_ID" ]; then
  ok "calendário nativo criado sem OAuth"
else
  bad "calendário nativo não foi criado: $(echo "$CALS" | head -c 120)"
fi

if echo "$CALS" | grep -q '"is_primary":true'; then
  ok "tem calendário primário"
else
  bad "nenhum calendário primário"
fi

# ---------------------------------------------------------------
head_ "2. Criação e classificação de flexibilidade (§5.6)"

# "Prova" deve virar FIXED pela heurística, sem o cliente pedir.
PROVA=$(curl -s -X POST "$API/calendar/events" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d '{"title":"Prova de C\u00e1lculo","start":"2027-06-20T14:00:00-03:00","end":"2027-06-20T17:00:00-03:00"}')
PROVA_FLEX=$(echo "$PROVA" | jq_ "d['flexibility']")

if [ "$PROVA_FLEX" = "fixed" ]; then
  ok "'Prova' classificada como fixa automaticamente"
else
  bad "'Prova' ficou '$PROVA_FLEX', deveria ser 'fixed'"
fi

ACAD=$(curl -s -X POST "$API/calendar/events" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d '{"title":"Academia","start":"2027-06-15T19:00:00-03:00","end":"2027-06-15T20:00:00-03:00"}')
if [ "$(echo "$ACAD" | jq_ "d['flexibility']")" = "flexible" ]; then
  ok "'Academia' classificada como flexível"
else
  bad "'Academia' mal classificada"
fi

# Evento inválido deve ser recusado na borda.
BAD_EV=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API/calendar/events" \
  "${AUTH[@]}" -H 'Content-Type: application/json' \
  -d '{"title":"Invertido","start":"2027-06-20T17:00:00-03:00","end":"2027-06-20T14:00:00-03:00"}')
if [ "$BAD_EV" = "400" ]; then
  ok "evento com fim antes do início é recusado"
else
  bad "evento invertido retornou $BAD_EV"
fi

# ---------------------------------------------------------------
head_ "3. Busca determinística de slots (§5.3)"

SLOTS=$(curl -s "$API/calendar/free-slots?start=2027-06-14T08:00:00-03:00&end=2027-06-19T22:00:00-03:00&duration_min=90&count=5" "${AUTH[@]}")
COUNT=$(echo "$SLOTS" | jq_ "d['count']")

if [ "${COUNT:-0}" -ge 1 ]; then
  ok "encontrou $COUNT slots livres"
else
  bad "nenhum slot encontrado"
fi

# A invariante que sustenta o produto: slot nunca colide com ocupado.
COLLIDES=$(echo "$SLOTS" | python -c "
import json,sys
d = json.load(sys.stdin)
# academia é 15/06 19:00-20:00
bad = [s for s in d['slots'] if s['start'] < '2027-06-15T20:00' and s['end'] > '2027-06-15T19:00']
print(len(bad))
" 2>/dev/null)
if [ "${COLLIDES:-1}" = "0" ]; then
  ok "nenhum slot colide com evento existente"
else
  bad "$COLLIDES slots colidem — a invariante central falhou"
fi

# Slots devem ser opções de verdade, não vizinhos de 15 min.
# Slots devem ser opções distintas, não vizinhos de 15 min: a
# diversificação limita a um por (dia, bloco de 3h).
DISTINCT=$(echo "$SLOTS" | python -c "
import json,sys
d = json.load(sys.stdin)
buckets = {(s['start'][:10], s['start'][11:13]) for s in d['slots']}
print(len(buckets))
" 2>/dev/null)
if [ "${DISTINCT:-0}" -ge 3 ]; then
  ok "slots diversificados ($DISTINCT turnos distintos)"
else
  bad "slots pouco diversificados ($DISTINCT turnos)"
fi

# Prazo respeitado: nada depois dele.
DEADLINE_SLOTS=$(curl -s "$API/calendar/free-slots?start=2027-06-14T08:00:00-03:00&end=2027-06-25T22:00:00-03:00&duration_min=60&deadline=2027-06-18T12:00:00-03:00&count=20" "${AUTH[@]}")
AFTER=$(echo "$DEADLINE_SLOTS" | python -c "
import json,sys
d=json.load(sys.stdin)
print(len([s for s in d['slots'] if s['start'] > '2027-06-18T12:00']))
" 2>/dev/null)
if [ "${AFTER:-1}" = "0" ]; then
  ok "nenhum slot depois do prazo"
else
  bad "$AFTER slots depois do prazo"
fi

# Janela absurda deve ser recusada (§7.0).
HUGE=$(curl -s -o /dev/null -w "%{http_code}" \
  "$API/calendar/free-slots?start=2027-01-01T00:00:00-03:00&end=2028-01-01T00:00:00-03:00&duration_min=60" "${AUTH[@]}")
if [ "$HUGE" = "400" ]; then
  ok "janela acima de 90 dias é recusada"
else
  bad "janela de 1 ano retornou $HUGE"
fi

# ---------------------------------------------------------------
head_ "4. Carga por dia (§5.9.1)"

WORKLOAD=$(curl -s "$API/calendar/workload?start=2027-06-14T00:00:00-03:00&end=2027-06-21T00:00:00-03:00" "${AUTH[@]}")
if echo "$WORKLOAD" | grep -q '"busy_hours"'; then
  ok "carga por dia calculada"
else
  bad "workload falhou"
fi

TOTAL_HOURS=$(echo "$WORKLOAD" | python -c "
import json,sys
d=json.load(sys.stdin)
print(sum(x['busy_hours'] for x in d['days']))
" 2>/dev/null)
# Prova (3h) + academia (1h) = 4h na semana.
if [ "$TOTAL_HOURS" = "4.0" ]; then
  ok "soma de horas da semana correta (4h)"
else
  bad "soma de horas: $TOTAL_HOURS (esperado 4.0)"
fi

# ---------------------------------------------------------------
head_ "5. Plano: propor, validar, aplicar (§5.7, §5.8)"

USER_ID=$(curl -s "$API/auth/me" "${AUTH[@]}" | jq_ "d['id']")
CONV_ID=$(curl -s -X POST "$API/conversations" "${AUTH[@]}" \
  -H 'Content-Type: application/json' -d '{}' | jq_ "d['id']")
TURN_ID=$(python -c "import uuid; print(uuid.uuid4())")

# Insere um rascunho como o worker faria. Dois itens: um válido e um
# que choca com a prova (FIXED) — para testar a detecção.
psql_ "INSERT INTO ai.turns (id, user_id, conversation_id, status)
       VALUES ('$TURN_ID', '$USER_ID', '$CONV_ID', 'done');" >/dev/null

psql_ "INSERT INTO ai.plan_drafts (turn_id, user_id, conversation_id, summary, items)
VALUES ('$TURN_ID', '$USER_ID', '$CONV_ID', '2 sessoes',
'[{\"op\":\"CREATE\",\"reason\":\"bloco livre antes da prova\",\"payload\":{\"title\":\"Estudo: integrais\",\"start\":\"2027-06-17T14:00:00-03:00\",\"end\":\"2027-06-17T15:30:00-03:00\",\"flexibility\":\"flexible\"}},
  {\"op\":\"CREATE\",\"reason\":\"ultima revisao\",\"payload\":{\"title\":\"Estudo: revisao\",\"start\":\"2027-06-20T15:00:00-03:00\",\"end\":\"2027-06-20T16:00:00-03:00\",\"flexibility\":\"flexible\"}}]'::json);" >/dev/null

PLANS=$(curl -s "$API/plans" "${AUTH[@]}")
PLAN_ID=$(echo "$PLANS" | jq_ "d[0]['id']")

if [ -n "$PLAN_ID" ]; then
  ok "rascunho do worker promovido a plano"
else
  bad "promoção falhou: $(echo "$PLANS" | head -c 150)"
  printf '\n\033[31mM2 incompleto.\033[0m\n\n'; exit 1
fi

# `reason` obrigatório em todo item (§5.6).
NO_REASON=$(echo "$PLANS" | python -c "
import json,sys
d=json.load(sys.stdin)
print(len([i for i in d[0]['items'] if not i['reason'].strip()]))
" 2>/dev/null)
if [ "${NO_REASON:-1}" = "0" ]; then
  ok "todo item tem justificativa"
else
  bad "$NO_REASON itens sem reason"
fi

# O item que choca com a prova (FIXED) deve ser bloqueante.
BLOCKING=$(echo "$PLANS" | python -c "
import json,sys
d=json.load(sys.stdin)
print(len([i for i in d[0]['items'] if i['is_blocking']]))
" 2>/dev/null)
if [ "${BLOCKING:-0}" -ge 1 ]; then
  ok "conflito com evento fixo detectado como bloqueante"
else
  bad "sobreposição com a prova NÃO foi detectada"
fi

# Aprovação granular: só o item válido.
GOOD_ITEM=$(echo "$PLANS" | python -c "
import json,sys
d=json.load(sys.stdin)
good = [i for i in d[0]['items'] if not i['is_blocking']]
print(good[0]['id'] if good else '')
" 2>/dev/null)

APPLY=$(curl -s -X POST "$API/plans/$PLAN_ID/apply" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d "{\"item_ids\":[\"$GOOD_ITEM\"]}")
APPLIED=$(echo "$APPLY" | jq_ "d['applied_count']")

if [ "${APPLIED:-0}" = "1" ]; then
  ok "aprovação granular: 1 de 2 itens aplicado"
else
  bad "aplicação granular falhou: $(echo "$APPLY" | head -c 200)"
fi

# O erro técnico não deve vazar para a resposta (§5.9.13).
if echo "$APPLY" | grep -qiE "asyncpg|sqlalchemy|Traceback"; then
  bad "ERRO TÉCNICO VAZOU na resposta da API"
else
  ok "nenhum detalhe técnico na resposta"
fi

EVENTS=$(curl -s "$API/calendar/events?start=2027-06-14T00:00:00-03:00&end=2027-06-21T00:00:00-03:00" "${AUTH[@]}")
if echo "$EVENTS" | grep -q "Estudo: integrais"; then
  ok "evento aprovado está na agenda"
else
  bad "evento aprovado não apareceu"
fi

if echo "$EVENTS" | grep -q "Estudo: revisao"; then
  bad "item BLOQUEADO entrou na agenda"
else
  ok "item bloqueado não entrou"
fi

# Criado pelo Age deve ser marcado: é o que permite saber o que mexer.
if echo "$EVENTS" | python -c "
import json,sys
d=json.load(sys.stdin)
e = next((x for x in d['events'] if 'integrais' in x['title']), None)
sys.exit(0 if e and e['created_by_age'] else 1)
" 2>/dev/null; then
  ok "evento marcado como criado pelo Age"
else
  bad "evento não marcado como criado pelo Age"
fi

# ---------------------------------------------------------------
head_ "6. Undo (§5.8)"

UNDO=$(curl -s -X POST "$API/plans/$PLAN_ID/undo" "${AUTH[@]}")
UNDONE=$(echo "$UNDO" | jq_ "d['undone_count']")

if [ "${UNDONE:-0}" -ge 1 ]; then
  ok "desfez $UNDONE mudança(s)"
else
  bad "undo falhou: $(echo "$UNDO" | head -c 200)"
fi

AFTER_UNDO=$(curl -s "$API/calendar/events?start=2027-06-14T00:00:00-03:00&end=2027-06-21T00:00:00-03:00" "${AUTH[@]}")
if echo "$AFTER_UNDO" | grep -q "Estudo: integrais"; then
  bad "evento continua na agenda após undo"
else
  ok "agenda voltou ao estado anterior"
fi

# O que o usuário criou à mão NÃO deve ser tocado pelo undo.
if echo "$AFTER_UNDO" | grep -q "Academia"; then
  ok "undo não tocou eventos que não criou"
else
  bad "UNDO APAGOU evento do usuário"
fi

# ---------------------------------------------------------------
head_ "7. Fixar e soltar evento (§8.1)"

ACAD_ID=$(echo "$ACAD" | jq_ "d['id']")
FIXED=$(curl -s -X PATCH "$API/calendar/events/$ACAD_ID/flexibility" "${AUTH[@]}" \
  -H 'Content-Type: application/json' -d '{"flexibility":"fixed"}')
if [ "$(echo "$FIXED" | jq_ "d['flexibility']")" = "fixed" ]; then
  ok "usuário pode fixar um evento"
else
  bad "fixar falhou"
fi

# ---------------------------------------------------------------
head_ "8. Isolamento (§4.2)"

OTHER=$(curl -s -X POST "$API/auth/register" -H 'Content-Type: application/json' \
  -d "{\"email\":\"outro-m2-$RANDOM@exemplo.com\",\"password\":\"$SENHA\"}" | jq_ "d['access_token']")

OTHER_EVENTS=$(curl -s "$API/calendar/events?start=2027-06-14T00:00:00-03:00&end=2027-06-21T00:00:00-03:00" \
  -H "Authorization: Bearer $OTHER")
if [ "$(echo "$OTHER_EVENTS" | jq_ "d['count']")" = "0" ]; then
  ok "outra conta não vê a agenda alheia"
else
  bad "VAZAMENTO: outra conta viu eventos"
fi

PEEK=$(curl -s -o /dev/null -w "%{http_code}" "$API/plans/$PLAN_ID" \
  -H "Authorization: Bearer $OTHER")
if [ "$PEEK" = "404" ]; then
  ok "outra conta não acessa plano alheio"
else
  bad "VAZAMENTO: plano alheio retornou $PEEK"
fi

# ---------------------------------------------------------------
head_ "Resultado"
printf '  %d passaram, %d falharam\n\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] && printf '\033[32mM2 validado.\033[0m\n\n' && exit 0
printf '\033[31mM2 incompleto.\033[0m\n\n' && exit 1
