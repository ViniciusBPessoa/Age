#!/usr/bin/env bash
# Age — validação da interface (§8.1, §8.2, §2.4.1, §4.1.1).
#
# Critério de pronto: as telas que §8.1 descreve existem, e a sessão
# atravessa o BFF sem o token chegar ao JavaScript da página.
#
# A diferença deste smoke em relação aos outros cinco: ele fala com o
# `web` na porta 3000, como um browser, e NÃO com o core-api direto. É
# o único que exercita o BFF — os anteriores passavam por cima dele, e
# por isso nenhum deles teria pego um erro de proxy, de cookie ou de
# renovação de token.
set -uo pipefail

WEB="http://localhost:3000"
API="http://localhost:8000/api/v1"
PASS=0
FAIL=0
EMAIL="smoke-ui-$RANDOM@exemplo.com"
SENHA="senha-de-smoke-123456"
JAR=$(mktemp)

ok()    { printf '  \033[32m✓\033[0m %s\n' "$1"; PASS=$((PASS+1)); }
bad()   { printf '  \033[31m✗\033[0m %s\n' "$1"; FAIL=$((FAIL+1)); }
head_() { printf '\n\033[1m%s\033[0m\n' "$1"; }

jq_() { python -c "import json,sys; d=json.load(sys.stdin); print($1)" 2>/dev/null; }

COMPOSE="docker compose -f infra/docker-compose.yml --env-file .env"
psql_() { $COMPOSE exec -T postgres psql -U age_super -d age -tAc "$1" 2>&1; }
uuid_() { grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1; }

# `-b/-c` no mesmo jar: o curl passa a se comportar como browser, o que
# é o ponto — a sessão desta UI vive em cookie, não em header.
web()   { curl -s -b "$JAR" -c "$JAR" "$@"; }
code_() { curl -s -o /dev/null -w '%{http_code}' -b "$JAR" -c "$JAR" "$@"; }

cleanup() { rm -f "$JAR"; }
trap cleanup EXIT

# Datas relativas a hoje, nunca literais.
#
# A primeira versão deste arquivo tinha "2026-11-20" escrito dentro, e
# isso funciona por algumas semanas: o prazo cai no horizonte de
# `/tasks/overview` e o teste passa. Depois sai do horizonte e o teste
# falha — reportando um bug no produto que não existe. Pior ainda, a
# falha parece real: "unscheduled_due_soon veio vazio" é exatamente o
# que um bug de verdade diria.
day_() { python -c "
import datetime
print((datetime.date.today() + datetime.timedelta(days=$1)).isoformat())
"; }
iso_() { python -c "
import datetime
d = datetime.date.today() + datetime.timedelta(days=$1)
print(f'{d.isoformat()}T$2:00:00-03:00')
"; }

# ---------------------------------------------------------------
head_ "1. As telas respondem"

for route in /login /register; do
  if [ "$(code_ "$WEB$route")" = "200" ]; then
    ok "$route abre sem sessão"
  else
    bad "$route devolveu $(code_ "$WEB$route")"
  fi
done

# A raiz decide para onde ir: sem sessão, login.
ROOT=$(curl -s -o /dev/null -w '%{redirect_url}' "$WEB/")
if [[ "$ROOT" == *"/login"* ]]; then
  ok "a raiz manda ao login quando não há sessão"
else
  bad "a raiz foi para '$ROOT'"
fi

# Tela de app sem sessão não deve renderizar: tem de redirecionar.
APP=$(curl -s -o /dev/null -w '%{redirect_url}' "$WEB/chat")
if [[ "$APP" == *"/login"* ]]; then
  ok "/chat sem sessão redireciona em vez de renderizar vazio"
else
  bad "/chat sem sessão foi para '$APP'"
fi

# REFRESH sem ACCESS — quem volta no dia seguinte: o access expira em
# 15 min, o refresh continua no navegador. É o único caso em que o
# layout passa do `hasSessionCookie()` (que aceita os dois) e chega ao
# `apiGet` sem credencial.
#
# Os dois testes acima NÃO cobrem isto: sem cookie algum, o layout
# corta antes; com access inválido, o core-api responde 401 e o fluxo
# de `Unauthenticated` segue normal. Só aqui o `authHeaders()` lança
# ANTES do fetch — e foi assim que uma retentativa que engolia essa
# exceção produziu três erros de tela cheia no lugar do redirect.
VENCIDA=$(curl -s -o /dev/null -w '%{http_code}' \
  -H 'Cookie: age_refresh=sessao-de-ontem' "$WEB/chat")
if [[ "$VENCIDA" == "307" || "$VENCIDA" == "302" ]]; then
  ok "/chat com refresh sem access redireciona (não dá 500)"
else
  bad "/chat com refresh sem access respondeu HTTP $VENCIDA"
fi

# ---------------------------------------------------------------
head_ "2. §4.1.1 — a credencial não chega ao browser"

REG=$(web -X POST "$WEB/api/auth/register" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$SENHA\",\"timezone\":\"America/Sao_Paulo\"}")

if echo "$REG" | grep -q '"ok"'; then
  ok "registro pelo BFF responde ok"
else
  bad "registro falhou: $(echo "$REG" | head -c 200)"
  printf '\n\033[31mInterface incompleta.\033[0m\n\n'; exit 1
fi

# O bug que este teste existe para impedir: o core-api devolve
# `access_token` NO CORPO. Um BFF de repasse puro entregaria isso ao
# browser, e o token estaria no JavaScript da página — exatamente o que
# §2.4.1 diz que o BFF existe para evitar.
if echo "$REG" | grep -q "access_token"; then
  bad "o access_token vazou no corpo da resposta ao browser"
else
  ok "o corpo da resposta NÃO carrega o token"
fi

if grep -q "age_access" "$JAR"; then
  ok "o access ficou em cookie"
else
  bad "cookie age_access não foi gravado"
fi

if grep -q "age_refresh" "$JAR"; then
  ok "o refresh do core-api atravessou o proxy"
else
  bad "cookie age_refresh ausente"
fi

# `HttpOnly` no jar do curl aparece com o prefixo `#HttpOnly_`.
if grep -q "#HttpOnly_.*age_access" "$JAR"; then
  ok "o cookie é httpOnly — o JS da página não o lê (§4.1.1)"
else
  bad "age_access não é httpOnly"
fi

# ---------------------------------------------------------------
head_ "3. O BFF autentica no core-api por conta própria"

ME=$(web "$WEB/api/auth/me")
if [ "$(echo "$ME" | jq_ "d['email']")" = "$EMAIL" ]; then
  ok "GET /api/auth/me funciona só com o cookie"
else
  bad "me falhou: $(echo "$ME" | head -c 200)"
fi

# O cliente não pode escolher a própria credencial: mandar um Bearer
# forjado daria a ele uma segunda forma de autenticar, e duas é uma a
# mais do que §4.1.1 define.
FORGED=$(curl -s -o /dev/null -w '%{http_code}' \
  -H "Authorization: Bearer nao-e-um-token-valido" "$WEB/api/auth/me")
if [ "$FORGED" = "401" ]; then
  ok "Bearer forjado sem cookie é recusado"
else
  bad "Bearer forjado devolveu $FORGED"
fi

# Rota que carrega credencial não passa pelo catch-all.
BYPASS=$(web -X POST "$WEB/api/auth/login" -H 'Content-Type: application/json' \
  -d '{"email":"x@y.z","password":"errada-mas-longa"}')
if echo "$BYPASS" | grep -qv "access_token"; then
  ok "login com senha errada não devolve token"
else
  bad "login com senha errada devolveu token"
fi

# ---------------------------------------------------------------
head_ "4. §8.1 — onboarding de 3 passos, retomável"

STEP=$(echo "$ME" | jq_ "d['onboarding_step']")
if [ "$STEP" = "0" ]; then
  ok "conta nova começa no passo 0"
else
  bad "onboarding_step inicial é $STEP"
fi

# Quem não terminou o onboarding é mandado para lá, em vez de cair num
# chat que não responde — a falha de produto mais provável aqui (§8.1).
ONB=$(curl -s -o /dev/null -w '%{redirect_url}' -b "$JAR" "$WEB/chat")
if [[ "$ONB" == *"/onboarding"* ]]; then
  ok "chat sem onboarding redireciona para o onboarding"
else
  bad "chat foi para '$ONB' em vez do onboarding"
fi

if [ "$(code_ "$WEB/onboarding")" = "200" ]; then
  ok "/onboarding abre"
else
  bad "/onboarding devolveu $(code_ "$WEB/onboarding")"
fi

# O passo é persistido para poder retomar.
web -X PATCH "$WEB/api/auth/me" -H 'Content-Type: application/json' \
  -d '{"onboarding_step":1}' >/dev/null
RESUMED=$(web "$WEB/api/auth/me" | jq_ "d['onboarding_step']")
if [ "$RESUMED" = "1" ]; then
  ok "o passo é gravado — recarregar retoma de onde parou"
else
  bad "o passo não persistiu (veio $RESUMED)"
fi

# Terminado o onboarding, o chat abre.
web -X PATCH "$WEB/api/auth/me" -H 'Content-Type: application/json' \
  -d '{"onboarding_step":3}' >/dev/null
if [ "$(code_ "$WEB/chat")" = "200" ]; then
  ok "com onboarding completo, /chat renderiza"
else
  bad "/chat devolveu $(code_ "$WEB/chat")"
fi

# Quem já terminou não vê o onboarding de novo.
DONE_ONB=$(curl -s -o /dev/null -w '%{redirect_url}' -b "$JAR" "$WEB/onboarding")
if [[ "$DONE_ONB" == *"/chat"* ]]; then
  ok "onboarding concluído manda ao chat em vez de repetir"
else
  bad "onboarding concluído foi para '$DONE_ONB'"
fi

# ---------------------------------------------------------------
head_ "5. Todas as telas de §8.1 renderizam autenticadas"

for route in /chat /calendar /tasks /study /routines /report /settings /settings/privacy /diagnostics; do
  got=$(code_ "$WEB$route")
  if [ "$got" = "200" ]; then
    ok "$route"
  else
    bad "$route devolveu $got"
  fi
done

# ---------------------------------------------------------------
head_ "6. §8.1 — a sidebar conta o que a conversa aplicou"

USERID=$(psql_ "SELECT id FROM core.users WHERE email='$EMAIL';" | uuid_)
CONV=$(web -X POST "$WEB/api/conversations" -H 'Content-Type: application/json' \
  -d '{"title":"Semana apertada"}' | jq_ "d['id']")

if [ -n "$CONV" ]; then
  ok "conversa criada pelo BFF"
else
  bad "não consegui criar conversa"
fi

# Um plano com um item de evento e um de tarefa, ambos APLICADOS.
PLAN=$(psql_ "INSERT INTO core.plans (user_id, conversation_id, status, summary, expires_at)
  VALUES ('$USERID','$CONV','applied','smoke', now() + interval '1 day')
  RETURNING id;" | uuid_)
psql_ "INSERT INTO core.plan_items
  (user_id, plan_id, target, op, reason, status, sequence)
  VALUES ('$USERID','$PLAN','event','CREATE','smoke','applied',0),
         ('$USERID','$PLAN','event','CREATE','smoke','applied',1),
         ('$USERID','$PLAN','task','CREATE','smoke','applied',2),
         ('$USERID','$PLAN','event','CREATE','smoke','rejected',3);" >/dev/null

LIST=$(web "$WEB/api/conversations?limit=10")
EV=$(echo "$LIST" | python -c "
import json,sys
d=json.load(sys.stdin)
for c in d['items']:
    if c['id'] == '$CONV':
        print(c['applied_events']); break
" 2>/dev/null)
TK=$(echo "$LIST" | python -c "
import json,sys
d=json.load(sys.stdin)
for c in d['items']:
    if c['id'] == '$CONV':
        print(c['applied_tasks']); break
" 2>/dev/null)

if [ "$EV" = "2" ]; then
  ok "conta 2 eventos aplicados (o 📅 da sidebar)"
else
  bad "applied_events veio '$EV', esperava 2"
fi
if [ "$TK" = "1" ]; then
  ok "conta 1 tarefa aplicada (o 📋 da sidebar)"
else
  bad "applied_tasks veio '$TK', esperava 1"
fi
# O item rejeitado não entra: aprovação é granular (§5.8), e contar o
# plano inteiro inflaria o número — contador que exagera o que o
# produto fez é pior que contador nenhum.
if [ "$EV" = "2" ]; then
  ok "item rejeitado NÃO é contado"
fi

# ---------------------------------------------------------------
head_ "7. §8.1 — o botão 'reservar tempo' gera o par ligado"

# Tarefa com prazo e estimativa, sem tempo reservado: o caso que §8.1
# marca com ⚠ na lista.
TASK=$(web -X POST "$WEB/api/tasks" -H 'Content-Type: application/json' \
  -d "{\"title\":\"Revisar capitulo 2\",\"due_date\":\"$(day_ 20)\",\"estimated_min\":90}" \
  | jq_ "d['id']")

if [ -n "$TASK" ]; then
  ok "tarefa criada pela UI"
else
  bad "criação de tarefa falhou"
fi

RESERVED_BEFORE=$(web "$WEB/api/tasks" | python -c "
import json,sys
for t in json.load(sys.stdin):
    if t['id'] == '$TASK': print(t['reserved_minutes']); break
" 2>/dev/null)
if [ "$RESERVED_BEFORE" = "0" ]; then
  ok "a tarefa nasce sem tempo reservado"
else
  bad "reserved_minutes já era '$RESERVED_BEFORE'"
fi

# Os horários livres vêm do Scheduler, deterministicamente (§5.3) — o
# botão não pede à pessoa que invente um horário.
SLOTS=$(web "$WEB/api/calendar/free-slots?start=$(iso_ 3 09)&end=$(iso_ 18 18)&duration_min=90&count=3")
SLOT_COUNT=$(echo "$SLOTS" | jq_ "d['count']")
if [ "${SLOT_COUNT:-0}" -ge 1 ]; then
  ok "o Scheduler devolveu $SLOT_COUNT horários livres"
else
  bad "nenhum horário livre: $(echo "$SLOTS" | head -c 200)"
fi

SLOT_START=$(echo "$SLOTS" | jq_ "d['slots'][0]['start']")
SLOT_END=$(echo "$SLOTS" | jq_ "d['slots'][0]['end']")

SCHED=$(web -X POST "$WEB/api/tasks/$TASK/schedule" -H 'Content-Type: application/json' \
  -d "{\"start\":\"$SLOT_START\",\"end\":\"$SLOT_END\",\"title\":\"Revisar capitulo 2\"}")
PLAN_ID=$(echo "$SCHED" | jq_ "d['plan_id']")

if [ -n "$PLAN_ID" ]; then
  ok "a reserva gerou um plano"
else
  bad "schedule falhou: $(echo "$SCHED" | head -c 250)"
fi

# O ponto que separa isto de um atalho perigoso: devolve PROPOSTA, não
# fato. §5.8 vale para o clique como vale para a conversa.
PLAN_STATUS=$(web "$WEB/api/plans/$PLAN_ID" | jq_ "d['status']")
if [ "$PLAN_STATUS" = "proposed" ]; then
  ok "o plano está 'proposed' — nada entrou na agenda ainda (§5.8)"
else
  bad "o plano veio como '$PLAN_STATUS'"
fi

DETAIL=$(web "$WEB/api/plans/$PLAN_ID")
ITEMS=$(echo "$DETAIL" | jq_ "len(d['items'])")
TARGETS=$(echo "$DETAIL" | python -c "
import json,sys
print(','.join(sorted(i['target'] for i in json.load(sys.stdin)['items'])))
" 2>/dev/null)
if [ "$ITEMS" = "2" ] && [ "$TARGETS" = "event,task" ]; then
  ok "dois itens: um de agenda e um de lista (§5.7.1)"
else
  bad "veio '$ITEMS' itens com targets '$TARGETS'"
fi

# O vínculo cruzado é o que faz a revisão tratar o par como unidade.
LINKED=$(echo "$DETAIL" | python -c "
import json,sys
items = json.load(sys.stdin)['items']
print('sim' if all(i['links'] for i in items) else 'nao')
" 2>/dev/null)
if [ "$LINKED" = "sim" ]; then
  ok "os dois itens apontam um para o outro — o par ligado"
else
  bad "os itens não estão ligados"
fi

# Aplicando, o bloco entra E a tarefa passa a ter tempo reservado.
APPLIED=$(web -X POST "$WEB/api/plans/$PLAN_ID/apply" \
  -H 'Content-Type: application/json' -d '{"confirm_blocking":true}')
APPLIED_N=$(echo "$APPLIED" | jq_ "d['applied_count']")
if [ "${APPLIED_N:-0}" -ge 2 ]; then
  ok "os dois itens foram aplicados juntos"
else
  bad "applied_count=$APPLIED_N: $(echo "$APPLIED" | head -c 250)"
fi

RESERVED_AFTER=$(web "$WEB/api/tasks" | python -c "
import json,sys
for t in json.load(sys.stdin):
    if t['id'] == '$TASK': print(t['reserved_minutes']); break
" 2>/dev/null)
if [ "${RESERVED_AFTER:-0}" -ge 90 ]; then
  ok "a tarefa agora mostra ${RESERVED_AFTER}min reservados"
else
  bad "reserved_minutes ficou '$RESERVED_AFTER'"
fi

# ---------------------------------------------------------------
head_ "8. §5.9.5 — a resposta que cruza as duas portas"

web -X POST "$WEB/api/tasks" -H 'Content-Type: application/json' \
  -d "{\"title\":\"Pagar IPVA\",\"due_date\":\"$(day_ 12)\",\"estimated_min\":20}" >/dev/null

# `horizon_days=30`, o mesmo que a tela pede. O default da rota é 7, e
# com ele um prazo no fim do mês não aparece — o que é correto para a
# pergunta "o que vence essa semana", e errado para o painel, que
# responde §5.9.5 para quem tem prova no fim do semestre.
OVER=$(web "$WEB/api/tasks/overview?horizon_days=30")
UNSCHED=$(echo "$OVER" | jq_ "len(d['unscheduled_due_soon'])")
EST=$(echo "$OVER" | jq_ "d['estimated_minutes_unscheduled']")
SUMMARY=$(echo "$OVER" | jq_ "d['summary_text']")

if [ "${UNSCHED:-0}" -ge 1 ]; then
  ok "o painel separa o que vence e NÃO tem tempo reservado"
else
  bad "unscheduled_due_soon veio vazio"
fi
# A estimativa soma só o que não tem horário: a tarefa já reservada
# ficou fora, senão o número diria "faltam 110min" para quem já
# reservou 90 deles.
if [ "$EST" = "20" ]; then
  ok "a estimativa soma só o sem horário (${EST}min)"
else
  bad "estimated_minutes_unscheduled=$EST, esperava 20"
fi
if [ -n "$SUMMARY" ]; then
  ok "a frase do painel vem pronta do domínio"
else
  bad "summary_text vazio"
fi

# ---------------------------------------------------------------
head_ "9. §5.10 — reconhecimento reativo, uma vez só"

CAL=$(psql_ "SELECT id FROM core.local_calendars WHERE user_id='$USERID' LIMIT 1;" | uuid_)
psql_ "INSERT INTO core.local_events
  (user_id, local_calendar_id, title, start_at, end_at, flexibility)
  VALUES ('$USERID','$CAL','Reuniao de orientacao',
          '$(day_ 25) 14:00-03','$(day_ 25) 15:00-03','fixed');" >/dev/null
EVID=$(psql_ "SELECT id FROM core.local_events WHERE user_id='$USERID'
  AND title='Reuniao de orientacao' LIMIT 1;" | uuid_)
psql_ "INSERT INTO core.external_change_log
  (user_id, change_kind, event_ref_kind, event_ref_id, title, starts_at, ends_at)
  VALUES ('$USERID','created','local','$EVID','Reuniao de orientacao',
          '$(day_ 25) 14:00-03','$(day_ 25) 15:00-03');" >/dev/null

CH1=$(web "$WEB/api/calendar/external-changes?acknowledge=true")
N1=$(echo "$CH1" | jq_ "len(d['changes'])")
TEXT1=$(echo "$CH1" | jq_ "d['summary_text']")

if [ "${N1:-0}" -ge 1 ]; then
  ok "a mudança externa aparece ao abrir o chat"
else
  bad "nenhuma mudança: $(echo "$CH1" | head -c 200)"
fi
if echo "$TEXT1" | grep -qi "orientacao\|orientação"; then
  ok "o texto nomeia o que apareceu"
else
  bad "o texto não cita o evento: $TEXT1"
fi
# O fuso: o evento é 14:00 em São Paulo. Se sair 17:00, está em UTC —
# e aí a pessoa conclui que o sistema está errado sobre a agenda dela.
if echo "$TEXT1" | grep -q "14:00"; then
  ok "a hora sai no fuso da conta, não em UTC"
else
  bad "hora fora do fuso da conta: $TEXT1"
fi

CH2=$(web "$WEB/api/calendar/external-changes")
N2=$(echo "$CH2" | jq_ "len(d['changes'])")
if [ "${N2:-1}" = "0" ]; then
  ok "reconhecida UMA vez — não volta em toda mensagem (§5.10)"
else
  bad "a mesma mudança voltou ($N2)"
fi

# ---------------------------------------------------------------
head_ "10. §8.1 — o cadeado da timeline"

LOCK=$(web -X PATCH "$WEB/api/calendar/events/$EVID/flexibility" \
  -H 'Content-Type: application/json' -d '{"flexibility":"flexible"}')
if [ "$(echo "$LOCK" | jq_ "d['flexibility']")" = "flexible" ]; then
  ok "soltar a flexibilidade funciona pela UI"
else
  bad "PATCH de flexibilidade falhou: $(echo "$LOCK" | head -c 200)"
fi

RELOCK=$(web -X PATCH "$WEB/api/calendar/events/$EVID/flexibility" \
  -H 'Content-Type: application/json' -d '{"flexibility":"fixed"}')
if [ "$(echo "$RELOCK" | jq_ "d['flexibility']")" = "fixed" ]; then
  ok "fixar de volta também — a heurística é corrigível (§5.6)"
else
  bad "não consegui fixar de volta"
fi

# ---------------------------------------------------------------
head_ "11. §8.1 — marcação rápida de rotina, sem chat"

ROUTINE=$(web -X POST "$WEB/api/routines" -H 'Content-Type: application/json' \
  -d "{\"title\":\"Academia\",\"rrule\":\"FREQ=WEEKLY;BYDAY=MO,WE,FR\",\"duration_min\":60,\"started_at\":\"$(iso_ -4 19)\",\"target_per_week\":3}")
RID=$(echo "$ROUTINE" | jq_ "d['id']")
if [ -n "$RID" ]; then
  ok "rotina criada pela UI"
else
  bad "criação de rotina falhou: $(echo "$ROUTINE" | head -c 250)"
fi

web -X POST "$WEB/api/routines/$RID/materialize" >/dev/null

OCC=$(web "$WEB/api/routines/$RID/occurrences?from=$(iso_ -5 00)&to=$(iso_ 11 00)")
OCC_N=$(echo "$OCC" | jq_ "len(d)")
if [ "${OCC_N:-0}" -ge 1 ]; then
  ok "$OCC_N ocorrências materializadas na janela"
else
  bad "nenhuma ocorrência"
fi

FIRST=$(echo "$OCC" | jq_ "d[0]['start']")
MARK=$(web -X POST "$WEB/api/routines/$RID/mark" -H 'Content-Type: application/json' \
  -d "{\"scheduled_start\":\"$FIRST\",\"status\":\"done\",\"actual_min\":55}")
if echo "$MARK" | grep -q "done"; then
  ok "marcar 'feito' funciona sem abrir o chat"
else
  bad "marcação falhou: $(echo "$MARK" | head -c 200)"
fi

# `paused` é QUERY param, não corpo. Mandar no corpo faria o FastAPI
# usar o default e nada mudaria — o botão pareceria funcionar.
PAUSED=$(web -X PATCH "$WEB/api/routines/$RID?paused=true")
if [ "$(echo "$PAUSED" | jq_ "d['paused_at']")" != "None" ]; then
  ok "pausar funciona, e pelo parâmetro certo"
else
  bad "pausar não teve efeito: $(echo "$PAUSED" | head -c 200)"
fi

RESUMED_R=$(web -X PATCH "$WEB/api/routines/$RID?paused=false")
if [ "$(echo "$RESUMED_R" | jq_ "d['paused_at']")" = "None" ]; then
  ok "retomar também — pausar não apaga (§5.9.4)"
else
  bad "retomar falhou"
fi

# §5.9.4: sem streak. A ausência é a feature.
ADH=$(web "$WEB/api/routines/$RID/adherence?days=28")
if echo "$ADH" | grep -qiE "streak|ofensiva|medalha"; then
  bad "a aderência tem campo de ofensiva — §5.9.4 proíbe"
else
  ok "aderência sem streak, como §5.9.4 decidiu"
fi
if echo "$ADH" | grep -q '"moved"'; then
  ok "'moved' é contado separado de 'skipped' — adaptar não é falhar"
else
  bad "campo moved ausente"
fi

# ---------------------------------------------------------------
head_ "12. §6.2.5 — os limites honestos vêm da API, não da tela"

LIM=$(web "$WEB/api/privacy/limits")
for field in llm backup external_events; do
  if [ -n "$(echo "$LIM" | jq_ "d['$field']")" ]; then
    ok "o aviso '$field' vem da API"
  else
    bad "aviso '$field' ausente"
  fi
done

# O número que a pessoa procura antes de apertar "apagar histórico":
# a agenda vai junto? Não.
PREV=$(web "$WEB/api/privacy/deletion-preview?scope=history")
DEL_EV=$(echo "$PREV" | jq_ "d['will_delete']['events']")
if [ "$DEL_EV" = "0" ]; then
  ok "o preview diz que apagar histórico NÃO toca a agenda"
else
  bad "o preview diz que apagaria $DEL_EV eventos"
fi

PRES=$(echo "$PREV" | jq_ "len(d['will_preserve'])")
if [ "${PRES:-0}" -ge 3 ]; then
  ok "o preview lista o que é PRESERVADO, não só o que sai"
else
  bad "preview sem lista de preservados"
fi

# ---------------------------------------------------------------
head_ "13. §7.1 — o streaming atravessa o BFF"

# O risco concreto desta seção: se o handler do Next fizesse
# `await response.text()` em vez de repassar o `ReadableStream`, o SSE
# morreria **sem erro nenhum** — a resposta chegaria inteira no fim e
# pareceria que o streaming nunca foi implementado. Os smokes M0 a M5
# falam com o core-api direto e por isso nenhum deles cobre este salto.
#
# O provedor é o `mock`: prova o encanamento sem gastar token de LLM.
CRED=$(web -X POST "$WEB/api/llm/credentials" -H 'Content-Type: application/json' \
  -d '{"provider_key":"mock","api_key":"mock-key-de-smoke"}')
CID=$(echo "$CRED" | jq_ "d['id']")
if [ -n "$CID" ]; then
  ok "credencial salva pela UI"
else
  bad "credencial falhou: $(echo "$CRED" | head -c 200)"
fi

VER=$(web -X POST "$WEB/api/llm/credentials/$CID/verify")
if [ "$(echo "$VER" | jq_ "d['ok']")" = "True" ]; then
  ok "verificação imediata funciona (§8.1 não deixa avançar sem ela)"
else
  bad "verificação falhou: $(echo "$VER" | head -c 200)"
fi

CONV2=$(web -X POST "$WEB/api/conversations" -H 'Content-Type: application/json' \
  -d '{"provider_key":"mock","model":"mock-chat"}' | jq_ "d['id']")
TURN=$(web -X POST "$WEB/api/conversations/$CONV2/messages" \
  -H 'Content-Type: application/json' \
  -d '{"content":"o que eu tenho pra fazer essa semana?"}')
TID=$(echo "$TURN" | jq_ "d['turn_id']")
if [ -n "$TID" ]; then
  ok "turno aceito (202) com turn_id"
else
  bad "turno recusado: $(echo "$TURN" | head -c 250)"
fi

# Lê o stream PELO BFF, na porta 3000.
STREAM=$(timeout 25 curl -sN -b "$JAR" "$WEB/api/turns/$TID/stream" 2>/dev/null | head -60)

if echo "$STREAM" | grep -q "^event: token"; then
  ok "eventos SSE chegam pelo BFF"
else
  bad "nenhum evento de token: $(echo "$STREAM" | head -c 200)"
fi

# Mais de um bloco `id:` prova que chegou em PEDAÇOS, e não de uma vez
# no fim — que é o sintoma de um proxy que bufferiza.
CHUNKS=$(echo "$STREAM" | grep -c "^id: ")
if [ "${CHUNKS:-0}" -ge 3 ]; then
  ok "chegou em $CHUNKS pedaços — o stream não foi bufferizado"
else
  bad "só $CHUNKS pedaço(s): o BFF pode estar materializando a resposta"
fi

# Os ids sequenciais são o que torna a retomada possível (§7.1): perder
# o Wi-Fi no meio do turno é reler do último id visto.
if echo "$STREAM" | grep -qE "^id: [0-9]+-[0-9]+"; then
  ok "os eventos têm id do Redis Stream — dá para retomar"
else
  bad "eventos sem id sequencial"
fi

# ---------------------------------------------------------------
head_ "14. §4.1.2 — renovação no servidor, invisível ao browser"

# Apaga só o cookie de access, deixando o refresh. É o estado de quem
# ficou 15 minutos parado: o BFF tem de renovar e repetir a chamada,
# sem o browser saber de nada.
grep -v "age_access" "$JAR" > "$JAR.tmp" && mv "$JAR.tmp" "$JAR"
if ! grep -q "age_access" "$JAR"; then
  ok "access removido do jar, refresh mantido"
else
  bad "não consegui remover o access do jar"
fi

RENEWED=$(web "$WEB/api/auth/me")
if [ "$(echo "$RENEWED" | jq_ "d['email']")" = "$EMAIL" ]; then
  ok "a chamada funcionou mesmo sem access — o BFF renovou (§4.1.2)"
else
  bad "sem access a chamada falhou: $(echo "$RENEWED" | head -c 200)"
fi

if grep -q "age_access" "$JAR"; then
  ok "o cookie novo foi gravado na resposta"
else
  bad "o access renovado não voltou como cookie"
fi

# O refresh rotacionado também tem de ser repassado: senão a PRÓXIMA
# renovação usa um token já consumido, a detecção de reuso dispara e a
# sessão legítima cai — um bug que só aparece 15 minutos depois.
grep -v "age_access" "$JAR" > "$JAR.tmp" && mv "$JAR.tmp" "$JAR"
AGAIN=$(web "$WEB/api/auth/me")
if [ "$(echo "$AGAIN" | jq_ "d['email']")" = "$EMAIL" ]; then
  ok "a segunda renovação também funciona (refresh rotacionado em dia)"
else
  bad "a segunda renovação falhou — o refresh rotacionado não foi repassado"
fi

# ---------------------------------------------------------------
head_ "15. Sair encerra de verdade"

web -X POST "$WEB/api/auth/logout" >/dev/null
AFTER=$(code_ "$WEB/api/auth/me")
if [ "$AFTER" = "401" ]; then
  ok "depois do logout, a API recusa"
else
  bad "depois do logout, /me devolveu $AFTER"
fi

GONE=$(curl -s -o /dev/null -w '%{redirect_url}' -b "$JAR" "$WEB/chat")
if [[ "$GONE" == *"/login"* ]]; then
  ok "e a tela volta ao login"
else
  bad "/chat foi para '$GONE'"
fi

# ---------------------------------------------------------------
head_ "16. Rate limit no login (§2219, §9.1)"

# Limpa as chaves antes: sem isto o teste depende de quantas tentativas
# rodaram nos 15 minutos anteriores, e passa ou falha por acaso.
redis_() { $COMPOSE exec -T redis sh -c 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning '"$1" 2>/dev/null; }
rl_limpa_() { redis_ '--scan --pattern "rl:*" | xargs -r redis-cli -a "$REDIS_PASSWORD" --no-auth-warning DEL' >/dev/null; }

rl_limpa_

# O limite de produção é 5/900 por e-mail. O alvo é um e-mail que NÃO
# existe: o que está sendo medido é a contenção de força bruta, e ela
# não pode depender de a conta existir — senão descobrir se um e-mail
# tem conta já seria um oráculo.
ALVO="forca-bruta-$RANDOM@exemplo.com"
RL_HDR=$(mktemp)
BLOQUEIO=0
for i in $(seq 1 7); do
  C=$(curl -s -o /dev/null -w '%{http_code}' -D "$RL_HDR" \
      -X POST "$WEB/api/auth/login" -H 'Content-Type: application/json' \
      -d "{\"email\":\"$ALVO\",\"password\":\"senha-errada-123456\"}")
  if [ "$C" = "429" ] && [ "$BLOQUEIO" = "0" ]; then BLOQUEIO=$i; fi
done

if [ "$BLOQUEIO" = "6" ]; then
  ok "o 6º login errado é bloqueado com 429 (5 passam, como configurado)"
elif [ "$BLOQUEIO" = "0" ]; then
  bad "sete logins errados seguidos e nenhum 429 — não há rate limit"
else
  bad "o 429 veio na tentativa $BLOQUEIO, esperado na 6ª"
fi

# `Retry-After` é o único campo que o cliente HTTP respeita sozinho, e
# ele atravessa DUAS camadas: core-api -> BFF -> browser. Esta checagem
# existe porque ele se perdia na segunda: `proxyTokenRoute` montava a
# resposta de erro com uma lista fixa de headers, e o header sumia em
# silêncio. O catch-all de `[...path]` copia tudo e não tinha o
# problema, então só login e registro eram afetados.
RA=$(grep -i '^retry-after' "$RL_HDR" | tr -d '\r' | cut -d' ' -f2)
rm -f "$RL_HDR"
if [ -n "$RA" ] && [ "$RA" -ge 1 ] 2>/dev/null; then
  ok "o 429 traz Retry-After ($RA s) atravessando o BFF"
else
  bad "o 429 chegou sem Retry-After — o cliente não sabe quanto esperar"
fi

# E o corpo no formato de erro de §7.3, para o front poder mapear o code.
CORPO=$(curl -s -X POST "$WEB/api/auth/login" -H 'Content-Type: application/json' \
        -d "{\"email\":\"$ALVO\",\"password\":\"senha-errada-123456\"}")
if [ "$(echo "$CORPO" | jq_ "d['detail']['error']['code']")" = "rate_limited" ]; then
  ok "o corpo traz code=rate_limited no formato de §7.3"
else
  bad "o corpo do 429 não tem code=rate_limited"
fi

# O que mais importa NÃO quebrar: quem sabe a senha não é força bruta.
# Sem o reset no sucesso, errar algumas vezes e depois acertar deixaria a
# conta a um erro do bloqueio — e trocar de dispositivo bloquearia o
# acesso legítimo.
rl_limpa_
for i in 1 2 3 4; do
  curl -s -o /dev/null -X POST "$WEB/api/auth/login" \
    -H 'Content-Type: application/json' \
    -d "{\"email\":\"$EMAIL\",\"password\":\"errada-123456\"}"
done
CERTA=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$WEB/api/auth/login" \
        -H 'Content-Type: application/json' \
        -d "{\"email\":\"$EMAIL\",\"password\":\"$SENHA\"}")
DEPOIS=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$WEB/api/auth/login" \
         -H 'Content-Type: application/json' \
         -d "{\"email\":\"$EMAIL\",\"password\":\"errada-123456\"}")
if [ "$CERTA" = "200" ] && [ "$DEPOIS" != "429" ]; then
  ok "acertar a senha zera a contagem (4 erros + acerto + erro, sem bloqueio)"
else
  bad "o acerto não zerou: login certo=$CERTA, erro seguinte=$DEPOIS"
fi

# Deixa limpo para quem rodar depois: as chaves têm TTL de 15 min, e um
# smoke que bloqueia o próximo não serve.
rl_limpa_

# ---------------------------------------------------------------
head_ "17. O trace_id atravessa os serviços (§12, §14.2)"

# §14.2 lista como risco número um a complexidade operacional de três
# serviços, e nomeia esta propriedade como a mitigação. O teste é pelo
# NAVEGADOR de propósito: o trace nasce no BFF, e medir só a porta 8000
# deixaria de fora justamente o salto que ele atravessa primeiro.
TRACE_SMOKE="smoke-trace-$RANDOM-$RANDOM"

TR_HDR=$(curl -s -D - -o /dev/null -X POST "$WEB/api/auth/login"          -H 'Content-Type: application/json'          -H "x-trace-id: $TRACE_SMOKE"          -d "{\"email\":\"$EMAIL\",\"password\":\"$SENHA\"}"          | grep -i '^x-trace-id:' | tr -d '
' | awk '{print $2}')

if [ "$TR_HDR" = "$TRACE_SMOKE" ]; then
  ok "o BFF devolve o trace na resposta (quem reporta erro pode citá-lo)"
else
  bad "o trace não voltou na resposta: '$TR_HDR'"
fi

# O log do core-api tem de conter o id, em JSON de uma linha. Sem isto a
# propagação é decorativa: o id viaja e não há onde procurá-lo.
sleep 1
LINHA=$($COMPOSE logs --no-log-prefix core-api 2>/dev/null         | grep -F "$TRACE_SMOKE" | tail -1)
if [ -n "$LINHA" ]; then
  ok "o core-api registra o trace no log"
else
  bad "nenhuma linha de log do core-api com o trace"
fi

# JSON válido importa porque o envelope é montado por nós: a mensagem do
# uvicorn contém aspas, e com formato interpolado em vez de serializado
# a linha saía inválida — descartada em silêncio por um agregador.
if [ -n "$LINHA" ] && printf '%s' "$LINHA"    | python -c "import json,sys; json.loads(sys.stdin.read())" 2>/dev/null; then
  ok "a linha de log é JSON válido mesmo com aspas na mensagem"
else
  bad "a linha de log do core-api não é JSON válido"
fi

# E o salto pela fila: §12 diz que é onde o trace quebra se não for
# propagado no payload, e era exatamente o que acontecia — o worker
# recebia o campo e o descartava.
# Sessão nova: a seção 15 fez logout e a 16 esgotou o rate limit de
# login desta conta. Reaproveitar o jar aqui testaria o estado deixado
# pelas seções anteriores, não o trace — e foi o que falhou na primeira
# versão desta seção.
EMAIL_TR="smoke-trace-$RANDOM@exemplo.com"
web -o /dev/null -X POST "$WEB/api/auth/register"     -H 'Content-Type: application/json'     -d "{\"email\":\"$EMAIL_TR\",\"password\":\"$SENHA\"}" >/dev/null

CONV_TR=$(web -X POST "$WEB/api/conversations" -H 'Content-Type: application/json'           -H "x-trace-id: $TRACE_SMOKE" -d '{}' | uuid_)
if [ -n "$CONV_TR" ]; then
  web -o /dev/null -X POST "$WEB/api/conversations/$CONV_TR/messages"       -H 'Content-Type: application/json' -H "x-trace-id: $TRACE_SMOKE"       -d '{"content":"smoke de rastreio"}' >/dev/null
  sleep 5
  if $COMPOSE logs --no-log-prefix ai-worker 2>/dev/null      | grep -qF "$TRACE_SMOKE"; then
    ok "o trace chega ao ai-worker pela fila (o salto de §12)"
  else
    bad "o trace não chegou ao worker: o salto pela fila está quebrado"
  fi
else
  bad "não consegui criar conversa para testar o trace pela fila"
fi

# ---------------------------------------------------------------
printf '\n\033[1m%s\033[0m\n' "Resultado"
printf '  %s passaram, %s falharam\n\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
  printf '\033[31mInterface incompleta.\033[0m\n\n'
  exit 1
fi
printf '\033[32mInterface validada: as telas de §8.1 existem e a sessão\n'
printf 'atravessa o BFF sem a credencial chegar ao browser.\033[0m\n\n'
