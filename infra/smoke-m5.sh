#!/usr/bin/env bash
# Age — validação do M5 (rotinas, replanejamento e relatório).
#
# Critério de pronto (PROGRESSO.md): "surgiu reunião terça 14h,
# reorganiza" propõe mover o flexível, e "como foi minha semana?" acha
# o padrão.
#
# O foco é no que o M5 acrescenta de próprio: rotina com hora marcada
# que ocupa a agenda, aderência sem streak, relatório que procura
# padrão em vez de só somar, e reação passiva a mudança externa.
set -uo pipefail

API="http://localhost:8000/api/v1"
PASS=0
FAIL=0
EMAIL="smoke-m5-$RANDOM@exemplo.com"
SENHA="senha-de-smoke-123456"

ok()    { printf '  \033[32m✓\033[0m %s\n' "$1"; PASS=$((PASS+1)); }
bad()   { printf '  \033[31m✗\033[0m %s\n' "$1"; FAIL=$((FAIL+1)); }
head_() { printf '\n\033[1m%s\033[0m\n' "$1"; }

jq_() { python -c "import json,sys; d=json.load(sys.stdin); print($1)" 2>/dev/null; }

COMPOSE="docker compose -f infra/docker-compose.yml --env-file .env"
psql_() { $COMPOSE exec -T postgres psql -U age_super -d age -tAc "$1" 2>&1; }
uuid_() { grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1; }

# ---------------------------------------------------------------
head_ "1. Conta e agenda de base"

TOKEN=$(curl -s -X POST "$API/auth/register" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$SENHA\"}" | jq_ "d['access_token']")

if [ -z "$TOKEN" ]; then
  bad "registro falhou"
  printf '\n\033[31mM5 incompleto.\033[0m\n\n'; exit 1
fi
AUTH=(-H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json')
ok "conta criada"

curl -s "$API/routines" "${AUTH[@]}" >/dev/null
USERID=$(psql_ "SELECT id FROM core.users WHERE email = '$EMAIL';" | uuid_)
CAL=$(psql_ "SELECT id FROM core.local_calendars WHERE user_id='$USERID' LIMIT 1;" | uuid_)
if [ -z "$CAL" ]; then
  CAL=$(psql_ "INSERT INTO core.local_calendars
    (user_id,name,timezone,is_default)
    VALUES ('$USERID','Minha agenda','America/Sao_Paulo',true)
    RETURNING id;" | uuid_)
fi

# Duas provas às 19h, em semanas diferentes: é o cenário de §5.9.4.
psql_ "INSERT INTO core.local_events
  (user_id, local_calendar_id, title, start_at, end_at, flexibility)
  VALUES ('$USERID','$CAL','Prova de Calculo',
          '2026-11-04 19:00-03','2026-11-04 21:00-03','fixed'),
         ('$USERID','$CAL','Prova de Algebra',
          '2026-11-11 19:00-03','2026-11-11 21:00-03','fixed');" >/dev/null
ok "duas provas fixas às 19h na agenda"

# ---------------------------------------------------------------
head_ "2. Preview antes de criar (§5.9.4)"

PREV=$(curl -s -X POST "$API/routines/preview" "${AUTH[@]}" -d '{
  "title":"Academia","rrule":"FREQ=WEEKLY;BYDAY=MO,WE,FR",
  "duration_min":60,"started_at":"2026-11-02T19:00:00-03:00",
  "target_per_week":3}')

N_OCC=$(echo "$PREV" | jq_ "len(d['occurrences'])")
if [ "$N_OCC" = "12" ]; then
  ok "3x por semana em 4 semanas = 12 ocorrências"
else
  bad "ocorrências = $N_OCC (esperado 12)"
fi

WEEKDAYS=$(echo "$PREV" | python -c "
import json,sys,datetime
d=json.load(sys.stdin)
dias={datetime.datetime.fromisoformat(o['start']).weekday() for o in d['occurrences']}
print(','.join(str(x) for x in sorted(dias)))")
if [ "$WEEKDAYS" = "0,2,4" ]; then
  ok "cai exatamente em seg/qua/sex"
else
  bad "dias da semana: $WEEKDAYS (esperado 0,2,4)"
fi

N_CONF=$(echo "$PREV" | jq_ "len(d['conflicts'])")
N_BLOCK=$(echo "$PREV" | jq_ "d['blocking_count']")
if [ "$N_CONF" = "2" ] && [ "$N_BLOCK" = "2" ]; then
  ok "2 conflitos detectados, ambos bloqueantes (provas são fixas)"
else
  bad "conflitos=$N_CONF bloqueantes=$N_BLOCK (esperado 2 e 2)"
fi

NAMED=$(echo "$PREV" | jq_ "d['conflicts'][0]['with_title']")
if echo "$NAMED" | grep -qi "prova"; then
  ok "o conflito diz COM O QUE colide: '$NAMED'"
else
  bad "conflito sem nome do que colide: $NAMED"
fi

# RRULE inválida não cria série fantasma.
BADRR=$(curl -s -X POST "$API/routines/preview" "${AUTH[@]}" -d '{
  "title":"X","rrule":"ISSO NAO E RRULE","duration_min":60,
  "started_at":"2026-11-02T19:00:00-03:00"}')
if echo "$BADRR" | grep -qi "inválida\|invalida"; then
  ok "RRULE inválida é recusada com mensagem em português"
else
  bad "RRULE inválida aceita: $(echo "$BADRR" | head -c 150)"
fi

# ---------------------------------------------------------------
head_ "3. Criar a série"

R=$(curl -s -X POST "$API/routines" "${AUTH[@]}" -d '{
  "title":"Academia","rrule":"FREQ=WEEKLY;BYDAY=MO,WE,FR",
  "duration_min":60,"started_at":"2026-11-02T19:00:00-03:00",
  "target_per_week":3}')
RID=$(echo "$R" | jq_ "d['id']")

if [ -n "$RID" ]; then
  ok "série criada"
else
  bad "falha ao criar: $(echo "$R" | head -c 200)"
  printf '\n\033[31mM5 incompleto.\033[0m\n\n'; exit 1
fi

if [ "$(echo "$R" | jq_ "d['flexibility']")" = "flexible" ]; then
  ok "nasce FLEXÍVEL — o Age pode propor mover (§5.9.4)"
else
  bad "flexibilidade errada: $(echo "$R" | jq_ "d['flexibility']")"
fi

MATERIALIZED=$(curl -s "$API/routines/$RID/occurrences" "${AUTH[@]}" | jq_ "len(d)")
if [ "$MATERIALIZED" = "12" ]; then
  ok "12 ocorrências materializadas no banco"
else
  bad "ocorrências no banco: $MATERIALIZED (esperado 12)"
fi

# Materializar de novo é idempotente — roda a cada abertura da timeline.
AGAIN=$(curl -s -X POST "$API/routines/$RID/materialize?days=28" "${AUTH[@]}" | jq_ "d['created']")
if [ "$AGAIN" = "0" ]; then
  ok "rematerializar não duplica (índice único faz o trabalho)"
else
  bad "rematerializar criou $AGAIN linhas — aderência duplicaria"
fi

# ---------------------------------------------------------------
head_ "4. Registrar o que aconteceu (§5.2, registro de fato)"

M1=$(curl -s -X POST "$API/routines/$RID/mark" "${AUTH[@]}" \
  -d '{"scheduled_start":"2026-11-02T22:00:00Z","status":"done","actual_min":55}')
if echo "$M1" | grep -q '"adherence"'; then
  ok "marcar feita devolve a aderência atualizada"
else
  bad "mark não devolveu aderência: $(echo "$M1" | head -c 150)"
fi

curl -s -o /dev/null -X POST "$API/routines/$RID/mark" "${AUTH[@]}" \
  -d '{"scheduled_start":"2026-11-04T22:00:00Z","status":"skipped"}'
curl -s -o /dev/null -X POST "$API/routines/$RID/mark" "${AUTH[@]}" \
  -d '{"scheduled_start":"2026-11-06T22:00:00Z","status":"moved",
       "moved_to":"2026-11-07T22:00:00Z"}'
ok "uma pulada e uma movida registradas"

MOVED_NO_DEST=$(curl -s -X POST "$API/routines/$RID/mark" "${AUTH[@]}" \
  -d '{"scheduled_start":"2026-11-09T22:00:00Z","status":"moved"}')
if echo "$MOVED_NO_DEST" | grep -qi "para quando"; then
  ok "movida sem destino é recusada — senão não se distingue de pulada"
else
  bad "movida sem destino aceita: $(echo "$MOVED_NO_DEST" | head -c 150)"
fi

GHOST=$(curl -s -X POST "$API/routines/$RID/mark" "${AUTH[@]}" \
  -d '{"scheduled_start":"2030-01-01T10:00:00Z","status":"done"}')
if echo "$GHOST" | grep -qi "não encontrei\|materializada"; then
  ok "ocorrência inexistente dá mensagem acionável, não 500"
else
  bad "ocorrência fantasma: $(echo "$GHOST" | head -c 150)"
fi

# ---------------------------------------------------------------
head_ "5. Aderência, sem streak (§5.9.4)"

AD=$(curl -s "$API/routines/$RID/adherence?days=60" "${AUTH[@]}")
echo "$AD" | python -c "
import json,sys
d=json.load(sys.stdin)
assert d['planned'] == 12, f\"planned={d['planned']}\"
assert d['done'] == 1, f\"done={d['done']}\"
assert d['skipped'] == 1, f\"skipped={d['skipped']}\"
assert d['moved'] == 1, f\"moved={d['moved']}\"
assert d['total_minutes_done'] == 55, f\"min={d['total_minutes_done']}\"
" 2>/dev/null \
  && ok "12 planejadas · 1 feita · 1 pulada · 1 movida · 55min" \
  || bad "aderência errada: $(echo "$AD" | head -c 200)"

if [ "$(echo "$AD" | jq_ "d['moved']")" = "1" ] && \
   [ "$(echo "$AD" | jq_ "d['skipped']")" = "1" ]; then
  ok "movida conta separado de pulada — adaptar-se não é falhar"
else
  bad "movida e pulada confundidas"
fi

if echo "$AD" | grep -qiE '"streak"|"badge"|"medal"'; then
  bad "a aderência tem streak — §5.9.4 proíbe"
else
  ok "nenhum streak, medalha ou ofensiva no payload"
fi

if [ "$(echo "$AD" | jq_ "d['target_per_week']")" = "3" ]; then
  ok "meta declarada preservada (3 por semana)"
else
  bad "meta perdida"
fi

# ---------------------------------------------------------------
head_ "6. Pausar e retomar, nunca apagar (§5.9.4)"

curl -s -o /dev/null -X PATCH "$API/routines/$RID?paused=true" "${AUTH[@]}"
PAUSED=$(curl -s "$API/routines" "${AUTH[@]}" | jq_ "d[0]['paused_at']")
if [ "$PAUSED" != "None" ] && [ -n "$PAUSED" ]; then
  ok "série pausada"
else
  bad "pausa não registrada"
fi

STILL=$(curl -s "$API/routines/$RID/adherence?days=60" "${AUTH[@]}" | jq_ "d['done']")
if [ "$STILL" = "1" ]; then
  ok "a aderência histórica sobrevive à pausa"
else
  bad "histórico perdido ao pausar: done=$STILL"
fi

curl -s -o /dev/null -X PATCH "$API/routines/$RID?paused=false" "${AUTH[@]}"
RESUMED=$(curl -s "$API/routines" "${AUTH[@]}" | jq_ "d[0]['paused_at']")
if [ "$RESUMED" = "None" ]; then
  ok "retomada em um pedido"
else
  bad "não retomou: $RESUMED"
fi

curl -s -o /dev/null -X DELETE "$API/routines/$RID" "${AUTH[@]}"
ROWS=$(psql_ "SELECT count(*) FROM core.routine_occurrences
  WHERE user_id='$USERID';")
ENDED=$(psql_ "SELECT ended_at IS NOT NULL FROM core.routines
  WHERE id='$RID';")
if [ "$ENDED" = "t" ] && [ "$ROWS" -ge 12 ]; then
  ok "DELETE encerra a série e PRESERVA o histórico ($ROWS ocorrências)"
else
  bad "DELETE apagou o histórico: ended=$ENDED linhas=$ROWS"
fi

# ---------------------------------------------------------------
head_ "7. Relatório que procura padrão (§5.9.9)"

# Uma série nova com padrão claro: tudo de manhã é pulado.
R2=$(curl -s -X POST "$API/routines" "${AUTH[@]}" -d '{
  "title":"Corrida","rrule":"FREQ=DAILY","duration_min":30,
  "started_at":"2026-11-02T07:00:00-03:00"}')
RID2=$(echo "$R2" | jq_ "d['id']")

for D in 02 03 04; do
  curl -s -o /dev/null -X POST "$API/routines/$RID2/mark" "${AUTH[@]}" \
    -d "{\"scheduled_start\":\"2026-11-${D}T10:00:00Z\",\"status\":\"skipped\"}"
done
for D in 05 06 07; do
  curl -s -o /dev/null -X POST "$API/routines/$RID2/mark" "${AUTH[@]}" \
    -d "{\"scheduled_start\":\"2026-11-${D}T10:00:00Z\",\"status\":\"done\",
         \"actual_min\":30}"
done

REP=$(curl -s "$API/reports/period?start=2026-11-01T00:00:00Z&end=2026-12-01T00:00:00Z" "${AUTH[@]}")
SUMMARY=$(echo "$REP" | jq_ "d['summary_text']")

if echo "$SUMMARY" | grep -q "Academia"; then
  ok "o relatório lista as rotinas do período"
else
  bad "rotina ausente do relatório: $SUMMARY"
fi

if echo "$SUMMARY" | grep -qiE "parabéns|parabens|muito bem|ótimo|otimo"; then
  bad "o relatório elogia — §5.9.9 proíbe elogio que o dado não sustenta"
else
  ok "nenhum elogio vazio no relatório"
fi

if echo "$SUMMARY" | grep -qE "Período de [0-9]{2}/[0-9]{2}"; then
  ok "período formatado em pt-BR (dd/mm)"
else
  bad "formato de data errado: $SUMMARY"
fi

ROUTINES_IN_REP=$(echo "$REP" | jq_ "len(d['routines'])")
if [ "$ROUTINES_IN_REP" -ge 1 ]; then
  ok "$ROUTINES_IN_REP rotina(s) com ocorrência no período"
else
  bad "nenhuma rotina no relatório"
fi

# Rotina sem ocorrência no período não deve aparecer como linha de zeros.
EMPTY_PERIOD=$(curl -s "$API/reports/period?start=2025-01-01T00:00:00Z&end=2025-01-08T00:00:00Z" "${AUTH[@]}" | jq_ "len(d['routines'])")
if [ "$EMPTY_PERIOD" = "0" ]; then
  ok "período vazio não lista rotinas com zeros (ruído, não informação)"
else
  bad "linhas de zero no relatório: $EMPTY_PERIOD"
fi

# ---------------------------------------------------------------
head_ "8. Mudança externa, reativa e não repetitiva (§5.10)"

SVC=$($COMPOSE exec -T ai-worker python -c "
from app.calendar_client import _service_token
print(_service_token())" 2>/dev/null | tail -1)

# Um bloco que o AGE marcou, para haver conflito de verdade.
psql_ "INSERT INTO core.local_events
  (user_id, local_calendar_id, title, start_at, end_at, flexibility,
   created_by_age)
  VALUES ('$USERID','$CAL','Algebra Linear - Transformacoes',
          '2026-11-17 14:00-03','2026-11-17 16:00-03','flexible',true);" >/dev/null

curl -s -o /dev/null -X POST "http://localhost:8000/internal/routines/external-change" \
  -H "Authorization: Bearer $SVC" -H "X-Age-User-Id: $USERID" \
  -H 'Content-Type: application/json' \
  -d '{"change_kind":"created","title":"Reuniao de orientacao",
       "starts_at":"2026-11-17T14:00:00-03:00",
       "ends_at":"2026-11-17T16:00:00-03:00"}'

CH=$(curl -s "$API/calendar/external-changes" "${AUTH[@]}")
if [ "$(echo "$CH" | jq_ "d['conflict_count']")" = "1" ]; then
  ok "o conflito com o que o Age marcou é detectado"
else
  bad "conflito não detectado: $(echo "$CH" | head -c 200)"
fi

CH_TEXT=$(echo "$CH" | jq_ "d['summary_text']")
if echo "$CH_TEXT" | grep -q "Algebra Linear"; then
  ok "o texto NOMEIA o que foi atropelado"
else
  bad "texto sem o que colide: $CH_TEXT"
fi

if echo "$CH_TEXT" | grep -qE "(seg|ter|qua|qui|sex|sáb|dom) [0-9]{2}/[0-9]{2} às"; then
  ok "data em pt-BR e no fuso do usuário"
else
  bad "data em inglês ou em UTC: $CH_TEXT"
fi

# Reconhecer uma vez: sem isso a mesma reunião volta em toda mensagem.
curl -s -o /dev/null "$API/calendar/external-changes?acknowledge=true" "${AUTH[@]}"
AFTER=$(curl -s "$API/calendar/external-changes" "${AUTH[@]}" | jq_ "len(d['changes'])")
if [ "$AFTER" = "0" ]; then
  ok "reconhecida uma vez, não repete (§5.10: reativo, não perseguidor)"
else
  bad "ainda pendente depois de reconhecer: $AFTER"
fi

# ---------------------------------------------------------------
head_ "9. Isolamento e fronteira de escrita"

OTHER=$(curl -s -X POST "$API/auth/register" -H 'Content-Type: application/json' \
  -d "{\"email\":\"outro-m5-$RANDOM@exemplo.com\",\"password\":\"$SENHA\"}" \
  | jq_ "d['access_token']")
OTHER_ROUTINES=$(curl -s "$API/routines" -H "Authorization: Bearer $OTHER" | jq_ "len(d)")
if [ "$OTHER_ROUTINES" = "0" ]; then
  ok "outro usuário não vê rotina alheia"
else
  bad "vazamento: outro viu $OTHER_ROUTINES rotinas"
fi

CROSS=$(curl -s -o /dev/null -w "%{http_code}" \
  "$API/routines/$RID2/adherence" -H "Authorization: Bearer $OTHER")
if [ "$CROSS" = "404" ]; then
  ok "aderência de outro dá 404"
else
  bad "aderência cruzada devolveu $CROSS"
fi

W=$(psql_ "SELECT has_table_privilege('ai_worker','core.routines','INSERT');")
Rr=$(psql_ "SELECT has_table_privilege('ai_worker','core.routines','SELECT');")
if [ "$W" = "f" ] && [ "$Rr" = "t" ]; then
  ok "o worker lê rotina e NÃO escreve — garantido por GRANT"
else
  bad "privilégios errados: insert=$W select=$Rr"
fi

FORCED=$(psql_ "SELECT count(*) FROM pg_class c
  JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='core' AND c.relkind='r'
    AND c.relname IN ('routines','routine_occurrences','external_change_log')
    AND c.relforcerowsecurity;")
if [ "$FORCED" = "3" ]; then
  ok "as 3 tabelas do M5 com FORCE ROW LEVEL SECURITY"
else
  bad "tabelas com FORCE RLS: $FORCED (esperado 3)"
fi

# ---------------------------------------------------------------
head_ "10. As ferramentas chegam ao modelo (§5.2)"

TOOLS=$($COMPOSE exec -T ai-worker python -c "
import app.tasks.run_turn
from app.domain.tools.registry import _REGISTRY
print(len(_REGISTRY))" 2>/dev/null | tail -1)
if [ "${TOOLS:-0}" -ge 27 ]; then
  ok "$TOOLS ferramentas registradas (agenda + estudo + tarefa + rotina)"
else
  bad "só $TOOLS ferramentas — algum módulo não foi importado"
fi

for TOOL in get_routines get_routine_adherence get_period_report \
            get_external_changes propose_routine mark_routine_occurrence; do
  FOUND=$($COMPOSE exec -T ai-worker python -c "
import app.tasks.run_turn
from app.domain.tools.registry import _REGISTRY
print('$TOOL' in _REGISTRY)" 2>/dev/null | tail -1)
  [ "$FOUND" = "True" ] && ok "tool '$TOOL' disponível" \
    || bad "tool '$TOOL' não chega ao modelo"
done

RECORD=$($COMPOSE exec -T ai-worker python -c "
import app.tasks.run_turn
from app.domain.tools.registry import _REGISTRY, ToolCategory
t = _REGISTRY['mark_routine_occurrence']
print(t.category == ToolCategory.RECORD)" 2>/dev/null | tail -1)
if [ "$RECORD" = "True" ]; then
  ok "marcar ocorrência é REGISTRO DE FATO, não proposta (§5.2)"
else
  bad "categoria errada: o usuário teria que 'aprovar' que faltou"
fi

PROMPT=$($COMPOSE exec -T ai-worker python -c "
from app.prompts import build_system_prompt
p = build_system_prompt()
print(all(m in p for m in [
    'Rotinas recorrentes', 'sem streak', 'movida não é falta',
    'Replanejar', 'fora do Age']))" 2>/dev/null | tail -1)
if [ "$PROMPT" = "True" ]; then
  ok "system prompt tem as regras de rotina, replanejamento e §5.10"
else
  bad "system prompt sem as regras do M5"
fi

# ---------------------------------------------------------------
head_ "11. Privacidade: export e os limites honestos (§6.2.5, §9.6)"

LIM=$(curl -s "$API/privacy/limits" "${AUTH[@]}")
if echo "$LIM" | grep -qi "não volta"; then
  ok "diz que o enviado ao LLM não volta"
else
  bad "aviso sobre o LLM ausente"
fi
if echo "$LIM" | grep -qi "backup"; then
  ok "diz que backup tem retenção própria"
else
  bad "aviso sobre backup ausente"
fi
if echo "$LIM" | grep -qiE "não são apagados|nao sao apagados"; then
  ok "diz que eventos no calendário externo ficam"
else
  bad "aviso sobre eventos externos ausente"
fi
if [ "$(echo "$LIM" | jq_ "d['undo_window_days']")" = "30" ]; then
  ok "janela de 30 dias para desfazer"
else
  bad "janela errada"
fi

EXP=$(curl -s "$API/privacy/export" "${AUTH[@]}")
echo "$EXP" | python -c "
import json,sys
d=json.load(sys.stdin)
for k in ('account','conversations','events','tasks','routines','study'):
    assert k in d, k
assert d['routines'], 'sem rotinas no export'
assert d['routines'][0]['occurrences'], 'rotina sem ocorrencias'
" 2>/dev/null \
  && ok "export completo, com as ocorrências de cada rotina" \
  || bad "export incompleto: $(echo "$EXP" | head -c 150)"

PREV_H=$(curl -s "$API/privacy/deletion-preview?scope=history" "${AUTH[@]}")
DEL_EV=$(echo "$PREV_H" | jq_ "d['will_delete']['events']")
if [ "$DEL_EV" = "0" ]; then
  ok "apagar histórico não toca a agenda (§9.6)"
else
  bad "o preview diz que apagaria $DEL_EV eventos"
fi

PRESERVE=$(echo "$PREV_H" | jq_ "len(d['will_preserve'])")
if [ "${PRESERVE:-0}" -ge 3 ]; then
  ok "o preview diz o que é PRESERVADO, não só o que sai"
else
  bad "preview sem lista de preservados"
fi

WRONG=$(curl -s -X POST "$API/privacy/delete-account" "${AUTH[@]}" \
  -d '{"confirm_email":"nao-e-meu@exemplo.com"}')
if echo "$WRONG" | grep -qi "digite o e-mail"; then
  ok "apagar conta exige digitar o e-mail (clique acidental não basta)"
else
  bad "confirmação aceita e-mail errado: $(echo "$WRONG" | head -c 150)"
fi

DEL=$(curl -s -X POST "$API/privacy/delete-account" "${AUTH[@]}" \
  -d "{\"confirm_email\":\"$EMAIL\",\"understood_external_events\":true}")
if echo "$DEL" | grep -qi "expurgo definitivo"; then
  ok "conta encerrada, com a data do expurgo dita"
else
  bad "encerramento falhou: $(echo "$DEL" | head -c 200)"
fi

# O bug que este teste existe para impedir: o access token de 15 min
# continuava valendo depois de a conta ser encerrada. O refresh não
# renovava, então a janela era curta — mas "curta" não é "nenhuma", e
# quem acabou de encerrar a conta não espera que o acesso siga aberto.
STILL=$(curl -s -o /dev/null -w "%{http_code}" "$API/tasks" "${AUTH[@]}")
if [ "$STILL" = "401" ]; then
  ok "o access token para de funcionar na hora (não só o refresh)"
else
  bad "conta encerrada e o token ainda abre tudo: HTTP $STILL"
fi

REVOKED=$(psql_ "SELECT count(*) FROM core.sessions
  WHERE user_id='$USERID' AND revoked_reason='account_deleted';")
if [ "${REVOKED:-0}" -ge 1 ]; then
  ok "sessões revogadas com motivo registrado"
else
  bad "sessões não revogadas"
fi

# ---------------------------------------------------------------
printf '\n'
if [ "$FAIL" -eq 0 ]; then
  printf '\033[32m%d passaram, 0 falharam.\033[0m M5 validado.\n\n' "$PASS"
  exit 0
else
  printf '\033[31m%d passaram, %d falharam.\033[0m\n\n' "$PASS" "$FAIL"
  exit 1
fi
