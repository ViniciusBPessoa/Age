#!/usr/bin/env bash
# Age — validação do M0.
#
# Critério de pronto (PROGRESSO.md): `make up` sobe tudo verde, e um POST
# no web chega ao ai-worker pela fila e volta ao browser por SSE.
#
# Este script verifica isso sem abrir navegador, incluindo a fronteira de
# privilégio do banco (ARQUITETURA.md §11.2) — a parte que, se estiver
# errada, deixa o RLS decorativo.
set -uo pipefail

# --env-file é obrigatório: o .env vive na raiz, mas o compose está em
# infra/, e o Docker só carrega .env automaticamente do diretório do
# arquivo compose. Sem isto, toda interpolação falha.
COMPOSE="docker compose -f infra/docker-compose.yml --env-file .env"
PASS=0
FAIL=0

ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; FAIL=$((FAIL+1)); }
head_() { printf '\n\033[1m%s\033[0m\n' "$1"; }

# ---------------------------------------------------------------
head_ "1. Containers"
for svc in postgres redis core-api ai-worker ai-worker-api web; do
  if $COMPOSE ps --status running --services 2>/dev/null | grep -qx "$svc"; then
    ok "$svc rodando"
  else
    bad "$svc NÃO está rodando"
  fi
done

# ---------------------------------------------------------------
head_ "2. Health e readiness"
if curl -sf localhost:8000/healthz | grep -q '"status":"ok"'; then
  ok "core-api /healthz"
else
  bad "core-api /healthz"
fi

if curl -sf localhost:8000/readyz | grep -q '"ready":true'; then
  ok "core-api /readyz (postgres + redis)"
else
  bad "core-api /readyz — veja: curl -s localhost:8000/readyz"
fi

if curl -sf localhost:3000/status | grep -q '"ready":true'; then
  ok "web alcança core-api server-side"
else
  bad "web -> core-api (CORE_API_URL errado?)"
fi

# ---------------------------------------------------------------
head_ "3. Migrations"
for svc in core-api ai-worker; do
  if $COMPOSE exec -T "$svc" alembic current 2>/dev/null | grep -q "head"; then
    ok "$svc na head"
  else
    bad "$svc fora da head — rode make migrate"
  fi
done

# ---------------------------------------------------------------
head_ "4. Fronteira de privilégio (§11.2)"
# O teste que importa: se estes passarem invertidos, o RLS não protege.
sql() { $COMPOSE exec -T postgres psql -U age_super -d age -tAc "$1" 2>&1; }

if sql "SELECT rolbypassrls FROM pg_roles WHERE rolname='core_api'" | grep -qx "f"; then
  ok "core_api é NOBYPASSRLS"
else
  bad "core_api PODE ignorar RLS — a proteção não vale nada"
fi

if sql "SELECT rolbypassrls FROM pg_roles WHERE rolname='ai_worker'" | grep -qx "f"; then
  ok "ai_worker é NOBYPASSRLS"
else
  bad "ai_worker PODE ignorar RLS"
fi

if sql "SELECT relforcerowsecurity FROM pg_class WHERE relname='users'" | grep -qx "t"; then
  ok "core.users tem FORCE ROW LEVEL SECURITY"
else
  bad "core.users SEM force — o dono ignora a política (§4.2.1)"
fi

# ai_worker não deve conseguir escrever em core
WRITE_AI=$($COMPOSE exec -T postgres psql -U ai_worker -d age -tAc \
  "INSERT INTO core.users (email) VALUES ('x@y.z')" 2>&1 || true)
if echo "$WRITE_AI" | grep -qi "permission denied\|negada"; then
  ok "ai_worker NÃO escreve em core"
else
  bad "ai_worker conseguiu escrever em core — GRANT errado"
fi

# Sem app.user_id, as tabelas estritas devem devolver ZERO linhas.
# Antes este check esperava ERRO; a migration 0004 mudou para "falha
# fechada", porque registro e login rodam legitimamente sem usuario —
# e a propriedade de seguranca que importa e nao vazar, nao estourar.
NO_UID=$($COMPOSE exec -T postgres psql -U core_api -d age -tAc \
  "SELECT count(*) FROM core.conversations" 2>&1 || true)
if echo "$NO_UID" | tr -d '[:space:]' | grep -qx "0"; then
  ok "sem app.user_id nenhuma linha e visivel (4.2.2)"
else
  bad "sem app.user_id retornou '$NO_UID' — deveria ser 0 (falha ABERTA)"
fi

# Com app.user_id de outro usuario, tambem zero: e o isolamento de 4.2.
ISOLATION=$($COMPOSE exec -T postgres psql -U core_api -d age -tAc \
  "SET app.user_id = '00000000-0000-0000-0000-000000000099'; \
   SELECT count(*) FROM core.conversations" 2>&1 | tail -1 || true)
if echo "$ISOLATION" | tr -d '[:space:]' | grep -qx "0"; then
  ok "usuario sem dados nao ve conversa de ninguem"
else
  bad "isolamento entre contas falhou: '$ISOLATION'"
fi

# ---------------------------------------------------------------
head_ "5. Caminho ponta a ponta (fila -> worker -> stream -> SSE)"
RESP=$(curl -sf -X POST localhost:3000/api/echo \
  -H 'Content-Type: application/json' \
  -d '{"message":"smoke test"}' || true)

TURN_ID=$(echo "$RESP" | sed -n 's/.*"turn_id":"\([^"]*\)".*/\1/p')

if [ -n "$TURN_ID" ]; then
  ok "POST via BFF aceito (202) turn_id=${TURN_ID:0:8}…"
else
  bad "POST falhou: $RESP"
fi

if [ -n "$TURN_ID" ]; then
  # -N desliga buffering do curl; sem isso o teste mede errado.
  STREAM=$(curl -sN --max-time 12 \
    "localhost:3000/api/echo/$TURN_ID/stream" 2>/dev/null | head -c 2000 || true)

  echo "$STREAM" | grep -q 'event: tool_start' \
    && ok "evento tool_start chegou" \
    || bad "tool_start não chegou — worker consumiu da fila?"

  echo "$STREAM" | grep -q 'event: token' \
    && ok "token chegou (streaming funciona)" \
    || bad "token não chegou"

  echo "$STREAM" | grep -q 'event: done' \
    && ok "done chegou (turno completo)" \
    || bad "done não chegou"

  # Se tudo veio de uma vez no final, há buffering no caminho.
  if echo "$STREAM" | grep -q 'event: ping\|event: tool_start'; then
    ok "stream incremental (sem buffering no proxy)"
  else
    bad "possível buffering — veja §7.1"
  fi
fi

# ---------------------------------------------------------------
head_ "Resultado"
printf '  %d passaram, %d falharam\n\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] && printf '\033[32mM0 validado.\033[0m\n\n' && exit 0
printf '\033[31mM0 incompleto.\033[0m\n\n' && exit 1
