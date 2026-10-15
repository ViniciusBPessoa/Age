#!/usr/bin/env bash
# Age — validação do M4 (tarefas).
#
# Critério de pronto (PROGRESSO.md): "o que tenho pra fazer essa
# semana?" cruza agenda e lista, mostra o que não tem tempo reservado,
# e "encaixa" cria os pares.
#
# O foco é no que só existe por haver DUAS portas: o par ligado
# tarefa↔evento, a aprovação e a reversão em grupo, e a resposta que
# nenhum app de agenda ou to-do dá sozinho.
set -uo pipefail

API="http://localhost:8000/api/v1"
PASS=0
FAIL=0
EMAIL="smoke-m4-$RANDOM@exemplo.com"
SENHA="senha-de-smoke-123456"

ok()    { printf '  \033[32m✓\033[0m %s\n' "$1"; PASS=$((PASS+1)); }
bad()   { printf '  \033[31m✗\033[0m %s\n' "$1"; FAIL=$((FAIL+1)); }
head_() { printf '\n\033[1m%s\033[0m\n' "$1"; }

jq_() { python -c "import json,sys; d=json.load(sys.stdin); print($1)" 2>/dev/null; }

COMPOSE="docker compose -f infra/docker-compose.yml --env-file .env"
# As chaves de rate limit por IP são zeradas no início (§2219): este
# script cria várias contas e faz dezenas de logins do MESMO IP, o que
# sob os limites de produção o bloquearia a si mesmo. Zerar o contador é
# melhor que desligar o limite, porque a rota exercitada continua sendo
# a real, com o `enforce` no lugar — se o limite quebrar, o
# `smoke-ui.sh` acusa.
rl_reset_() {
  $COMPOSE exec -T redis sh -c     'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning --scan --pattern "rl:*:ip:*"      | xargs -r redis-cli -a "$REDIS_PASSWORD" --no-auth-warning DEL' >/dev/null 2>&1 || true
}
rl_reset_

psql_() { $COMPOSE exec -T postgres psql -U age_super -d age -tAc "$1" 2>&1; }
# Extrai só o uuid da saída do psql: com mais de um comando, `-tAc`
# também devolve linhas de status como "INSERT 0 1".
uuid_() { grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1; }

# ---------------------------------------------------------------
head_ "1. Conta e lista nativa"

TOKEN=$(curl -s -X POST "$API/auth/register" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$SENHA\"}" | jq_ "d['access_token']")

if [ -z "$TOKEN" ]; then
  bad "registro falhou"
  printf '\n\033[31mM4 incompleto.\033[0m\n\n'; exit 1
fi
AUTH=(-H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json')
ok "conta criada"

LISTS=$(curl -s "$API/tasks/lists" "${AUTH[@]}")
LIST_ID=$(echo "$LISTS" | jq_ "d[0]['id']")
IS_DEFAULT=$(echo "$LISTS" | jq_ "d[0]['is_default']")
if [ -n "$LIST_ID" ] && [ "$IS_DEFAULT" = "True" ]; then
  ok "lista nativa criada no primeiro acesso, marcada como default"
else
  bad "lista nativa ausente: $(echo "$LISTS" | head -c 150)"
fi

# Uma lista default por usuário, garantido pelo banco.
DEFAULTS=$(psql_ "SELECT count(*) FROM core.local_task_lists
  WHERE user_id = (SELECT id FROM core.users WHERE email = '$EMAIL')
    AND is_default;")
if [ "$DEFAULTS" = "1" ]; then
  ok "exatamente uma lista default (índice único parcial)"
else
  bad "listas default: $DEFAULTS (esperado 1)"
fi

# ---------------------------------------------------------------
head_ "2. CRUD de tarefa"

T1=$(curl -s -X POST "$API/tasks" "${AUTH[@]}" \
  -d '{"title":"Relatorio do TCC","due_date":"2026-11-20",
       "estimated_min":180,"priority":"high"}')
T1_ID=$(echo "$T1" | jq_ "d['id']")
if [ -n "$T1_ID" ] && [ "$(echo "$T1" | jq_ "d['estimated_min']")" = "180" ]; then
  ok "tarefa criada com estimativa e prioridade"
else
  bad "falha ao criar tarefa: $(echo "$T1" | head -c 200)"
fi

T2_ID=$(curl -s -X POST "$API/tasks" "${AUTH[@]}" \
  -d '{"title":"Revisar capitulo 2","due_date":"2026-11-10","estimated_min":90}' \
  | jq_ "d['id']")
T3_ID=$(curl -s -X POST "$API/tasks" "${AUTH[@]}" \
  -d '{"title":"Pagar IPVA","due_date":"2020-01-15"}' | jq_ "d['id']")
T4_ID=$(curl -s -X POST "$API/tasks" "${AUTH[@]}" \
  -d '{"title":"Comprar presente"}' | jq_ "d['id']")
[ -n "$T2_ID" ] && [ -n "$T3_ID" ] && [ -n "$T4_ID" ] \
  && ok "mais 3 tarefas: uma vencendo, uma atrasada, uma sem prazo" \
  || bad "falha ao criar as outras tarefas"

# Prazo vencido é marcado, mas a tarefa continua utilizável.
OVERDUE=$(curl -s "$API/tasks" "${AUTH[@]}" \
  | jq_ "[t['is_overdue'] for t in d if t['id']=='$T3_ID'][0]")
if [ "$OVERDUE" = "True" ]; then
  ok "tarefa com prazo passado marcada como atrasada"
else
  bad "atraso não detectado: $OVERDUE"
fi

# "até sexta" é sexta INTEIRA: tarefa só com data não vence às 00:00.
FUTURE=$(curl -s -X POST "$API/tasks" "${AUTH[@]}" \
  -d "{\"title\":\"Vence hoje\",\"due_date\":\"$(date -u +%Y-%m-%d)\"}")
if [ "$(echo "$FUTURE" | jq_ "d['is_overdue']")" = "False" ]; then
  ok "tarefa que vence HOJE não é atrasada (o dia inteiro conta)"
else
  bad "tarefa de hoje marcada como atrasada — o prazo virou 00:00"
fi

# ---------------------------------------------------------------
head_ "3. Coerência entre prazo com e sem hora"

WITH_TIME=$(curl -s -X PATCH "$API/tasks/$T2_ID" "${AUTH[@]}" \
  -d '{"due_at":"2026-11-12T15:00:00-03:00"}')
DUE_DATE=$(echo "$WITH_TIME" | jq_ "d['due_date']")
if [ "$DUE_DATE" = "2026-11-12" ]; then
  ok "due_at normaliza due_date na mesma entidade"
else
  bad "due_date não acompanhou due_at: $DUE_DATE"
fi

BACK_TO_DATE=$(curl -s -X PATCH "$API/tasks/$T2_ID" "${AUTH[@]}" \
  -d '{"due_date":"2026-11-14"}')
if [ "$(echo "$BACK_TO_DATE" | jq_ "d['due_at']")" = "None" ]; then
  ok "voltar para prazo sem hora limpa a hora pendurada"
else
  bad "a hora da terça ficou pendurada: $(echo "$BACK_TO_DATE" | jq_ "d['due_at']")"
fi

# ---------------------------------------------------------------
head_ "4. Panorama que cruza as duas fontes (§5.9.5)"

OV=$(curl -s "$API/tasks/overview?horizon_days=366" "${AUTH[@]}")
TOTAL=$(echo "$OV" | jq_ "d['total_open']")
NO_DUE=$(echo "$OV" | jq_ "d['without_due_count']")
UNSCHED=$(echo "$OV" | jq_ "len(d['unscheduled_due_soon'])")

[ "$TOTAL" = "5" ] && ok "5 tarefas abertas contadas" \
  || bad "total_open = $TOTAL (esperado 5)"
[ "$NO_DUE" = "1" ] && ok "a sem prazo aparece, separada" \
  || bad "without_due_count = $NO_DUE (esperado 1)"
[ "$UNSCHED" -ge 3 ] && ok "$UNSCHED tarefas sem tempo reservado" \
  || bad "unscheduled = $UNSCHED (esperado >= 3)"

# A soma só considera quem TEM estimativa: inventar número faria o
# "cabe" ser palpite apresentado como conta.
EST=$(echo "$OV" | jq_ "d['estimated_minutes_unscheduled']")
if [ "$EST" = "270" ]; then
  ok "estimativa somada = 270min (180+90), só de quem tem estimativa"
else
  bad "estimativa somada = $EST (esperado 270)"
fi

SUMMARY=$(echo "$OV" | jq_ "d['summary_text']")
if echo "$SUMMARY" | grep -q "SEM tempo reservado"; then
  ok "resumo diz explicitamente o que não tem tempo reservado"
else
  bad "resumo não distingue reservado: $SUMMARY"
fi

# ---------------------------------------------------------------
head_ "5. Reordenação preserva quem não foi citado (§5.7.3)"

curl -s -o /dev/null -X POST "$API/tasks/reorder" "${AUTH[@]}" \
  -d "{\"list_id\":\"$LIST_ID\",\"ordered_ids\":[\"$T3_ID\",\"$T1_ID\"]}"
ORDER=$(curl -s "$API/tasks" "${AUTH[@]}" \
  | python -c "
import json,sys
d=sorted(json.load(sys.stdin), key=lambda t: t['position'])
print(','.join(t['id'] for t in d))")
FIRST=$(echo "$ORDER" | cut -d, -f1)
SECOND=$(echo "$ORDER" | cut -d, -f2)
if [ "$FIRST" = "$T3_ID" ] && [ "$SECOND" = "$T1_ID" ]; then
  ok "as citadas foram para o começo, na ordem pedida"
else
  bad "ordem errada: $ORDER"
fi

REST=$(echo "$ORDER" | cut -d, -f3-)
if echo "$REST" | grep -q "$T2_ID" && echo "$REST" | grep -q "$T4_ID"; then
  ok "as não citadas ficaram depois, sem embaralhar"
else
  bad "as não citadas se perderam: $REST"
fi

# ---------------------------------------------------------------
head_ "6. Conclusão: explícita, idempotente, reversível"

DONE=$(curl -s -X POST "$API/tasks/$T4_ID/complete" "${AUTH[@]}")
AT1=$(echo "$DONE" | jq_ "d['completed_at']")
if [ "$(echo "$DONE" | jq_ "d['status']")" = "done" ] && [ -n "$AT1" ]; then
  ok "tarefa concluída, com hora do fato registrada"
else
  bad "conclusão falhou: $(echo "$DONE" | head -c 150)"
fi

AT2=$(curl -s -X POST "$API/tasks/$T4_ID/complete" "${AUTH[@]}" \
  | jq_ "d['completed_at']")
if [ "$AT1" = "$AT2" ]; then
  ok "concluir de novo preserva a hora original (duplo-clique é normal)"
else
  bad "a hora foi sobrescrita: $AT1 -> $AT2"
fi

OPEN_NOW=$(curl -s "$API/tasks" "${AUTH[@]}" | jq_ "len(d)")
if [ "$OPEN_NOW" = "4" ]; then
  ok "a concluída sai da lista de abertas"
else
  bad "abertas = $OPEN_NOW (esperado 4)"
fi

curl -s -o /dev/null -X POST "$API/tasks/$T4_ID/reopen" "${AUTH[@]}"
REOPENED=$(curl -s "$API/tasks" "${AUTH[@]}" \
  | jq_ "len([t for t in d if t['id']=='$T4_ID'])")
if [ "$REOPENED" = "1" ]; then
  ok "reabrir devolve a tarefa à lista (undo de COMPLETE)"
else
  bad "reabrir não funcionou"
fi

# ---------------------------------------------------------------
head_ "7. O par ligado tarefa↔evento (§5.7.1)"

# Um bloco na agenda para a tarefa do TCC, e o vínculo.
UID_DB=$(psql_ "SELECT id FROM core.users WHERE email = '$EMAIL';" | uuid_)
CAL_ID=$(curl -s "$API/calendar/calendars" "${AUTH[@]}" | jq_ "d[0]['id']" 2>/dev/null)
if [ -z "$CAL_ID" ]; then
  CAL_ID=$(psql_ "SELECT id FROM core.local_calendars WHERE user_id='$UID_DB' LIMIT 1;" | uuid_)
fi

EV_ID=$(psql_ "INSERT INTO core.local_events
  (user_id, local_calendar_id, title, start_at, end_at, flexibility, created_by_age)
  VALUES ('$UID_DB', '$CAL_ID', 'Relatorio do TCC',
          '2026-11-12 14:00+00', '2026-11-12 17:00+00', 'flexible', true)
  RETURNING id;" | uuid_)

psql_ "INSERT INTO core.task_event_links
    (user_id, task_ref_kind, task_ref_id, event_ref_kind, event_ref_id)
  VALUES ('$UID_DB','local_tasks','$T1_ID','local_events','$EV_ID');" >/dev/null

LINKED=$(curl -s "$API/tasks/$T1_ID/links" "${AUTH[@]}")
MINUTES=$(echo "$LINKED" | jq_ "d[0]['minutes']")
if [ "$MINUTES" = "180" ]; then
  ok "a tarefa sabe que tem 3h reservadas, e quando"
else
  bad "vínculo não resolvido: $(echo "$LINKED" | head -c 200)"
fi

# E o panorama passa a distinguir.
OV2=$(curl -s "$API/tasks/overview?horizon_days=366" "${AUTH[@]}")
HAS_RESERVED=$(echo "$OV2" | python -c "
import json,sys
d=json.load(sys.stdin)
todas = d['overdue'] + d['due_soon']
print(sum(1 for t in todas if t['reserved_minutes'] > 0))")
if [ "$HAS_RESERVED" = "1" ]; then
  ok "o panorama separa quem tem bloco de quem não tem"
else
  bad "tarefas com bloco no panorama: $HAS_RESERVED (esperado 1)"
fi

STILL_UNSCHED=$(echo "$OV2" | python -c "
import json,sys
d=json.load(sys.stdin)
print(','.join(t['title'] for t in d['unscheduled_due_soon']))")
if ! echo "$STILL_UNSCHED" | grep -q "Relatorio do TCC"; then
  ok "a que ganhou bloco saiu da lista de 'sem tempo'"
else
  bad "ainda listada como sem tempo: $STILL_UNSCHED"
fi

# O índice único impede reservar duas vezes o mesmo par.
DUP=$(psql_ "INSERT INTO core.task_event_links
    (user_id, task_ref_kind, task_ref_id, event_ref_kind, event_ref_id)
  VALUES ('$UID_DB','local_tasks','$T1_ID','local_events','$EV_ID');")
if echo "$DUP" | grep -qi "duplicate\|unique"; then
  ok "o banco recusa o mesmo par duas vezes"
else
  bad "par duplicado foi aceito: $DUP"
fi

# ---------------------------------------------------------------
head_ "8. Isolamento entre usuários (RLS)"

OTHER=$(curl -s -X POST "$API/auth/register" -H 'Content-Type: application/json' \
  -d "{\"email\":\"outro-m4-$RANDOM@exemplo.com\",\"password\":\"$SENHA\"}" \
  | jq_ "d['access_token']")
OTHER_TASKS=$(curl -s "$API/tasks" -H "Authorization: Bearer $OTHER" | jq_ "len(d)")
if [ "$OTHER_TASKS" = "0" ]; then
  ok "outro usuário não vê nenhuma tarefa alheia"
else
  bad "vazamento: outro usuário viu $OTHER_TASKS tarefas"
fi

NOT_MINE=$(curl -s -o /dev/null -w "%{http_code}" \
  -X PATCH "$API/tasks/$T1_ID" -H "Authorization: Bearer $OTHER" \
  -H 'Content-Type: application/json' -d '{"title":"invadido"}')
if [ "$NOT_MINE" = "404" ]; then
  ok "editar tarefa de outro dá 404, não 403 (não confirma existência)"
else
  bad "PATCH cruzado devolveu $NOT_MINE"
fi

# ---------------------------------------------------------------
head_ "9. Fronteira de escrita imposta pelo Postgres (§3.4)"

W=$(psql_ "SELECT has_table_privilege('ai_worker','core.local_tasks','INSERT');")
R=$(psql_ "SELECT has_table_privilege('ai_worker','core.local_tasks','SELECT');")
if [ "$W" = "f" ] && [ "$R" = "t" ]; then
  ok "o worker lê tarefa e NÃO escreve — garantido por GRANT"
else
  bad "privilégios do worker errados: insert=$W select=$R"
fi

FORCED=$(psql_ "SELECT count(*) FROM pg_class c
  JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='core' AND c.relkind='r'
    AND c.relname IN ('local_tasks','local_task_lists','tasks_shadow',
                      'task_connections','task_list_selections',
                      'task_event_links')
    AND c.relforcerowsecurity;")
if [ "$FORCED" = "6" ]; then
  ok "as 6 tabelas de tarefa com FORCE ROW LEVEL SECURITY"
else
  bad "tabelas com FORCE RLS: $FORCED (esperado 6)"
fi

# ---------------------------------------------------------------
head_ "10. Idempotência e erros acionáveis"

DEL1=$(curl -s -o /dev/null -w "%{http_code}" \
  -X DELETE "$API/tasks/$T3_ID" "${AUTH[@]}")
DEL2=$(curl -s -o /dev/null -w "%{http_code}" \
  -X DELETE "$API/tasks/$T3_ID" "${AUTH[@]}")
if [ "$DEL1" = "204" ] && [ "$DEL2" = "204" ]; then
  ok "delete é idempotente (importa para o undo)"
else
  bad "delete: $DEL1 depois $DEL2"
fi

GHOST=$(curl -s -X PATCH "$API/tasks/00000000-0000-0000-0000-000000000000" \
  "${AUTH[@]}" -d '{"title":"x"}')
if echo "$GHOST" | grep -qi "não existe"; then
  ok "tarefa inexistente devolve mensagem em português, sem stack"
else
  bad "erro técnico vazou: $(echo "$GHOST" | head -c 150)"
fi

if ! echo "$GHOST" | grep -qiE "sqlalchemy|asyncpg|traceback"; then
  ok "nenhum detalhe de driver na resposta (§5.9.13)"
else
  bad "detalhe interno exposto: $(echo "$GHOST" | head -c 200)"
fi

# ---------------------------------------------------------------
head_ "11. As ferramentas chegam ao modelo (§5.2)"

TOOLS=$($COMPOSE exec -T ai-worker python -c "
import app.tasks.run_turn
from app.domain.tools.registry import _REGISTRY
print(len(_REGISTRY))
" 2>/dev/null | tail -1)
if [ "${TOOLS:-0}" -ge 21 ]; then
  ok "$TOOLS ferramentas registradas (agenda + estudo + tarefa)"
else
  bad "só $TOOLS ferramentas registradas — algum módulo não foi importado"
fi

for TOOL in get_task_overview create_task complete_task reserve_time_for_task \
            propose_study_plan; do
  FOUND=$($COMPOSE exec -T ai-worker python -c "
import app.tasks.run_turn
from app.domain.tools.registry import _REGISTRY
print('$TOOL' in _REGISTRY)
" 2>/dev/null | tail -1)
  [ "$FOUND" = "True" ] && ok "tool '$TOOL' disponível" \
    || bad "tool '$TOOL' não chega ao modelo"
done

PROMPT=$($COMPOSE exec -T ai-worker python -c "
from app.prompts import build_system_prompt
p = build_system_prompt()
print(all(m in p for m in ['Agenda ou lista','Nunca conclua tarefa',
                           'reserve_time_for_task']))
" 2>/dev/null | tail -1)
if [ "$PROMPT" = "True" ]; then
  ok "system prompt tem as regras de destino e de conclusão"
else
  bad "system prompt sem as regras do M4"
fi

# ---------------------------------------------------------------
printf '\n'
if [ "$FAIL" -eq 0 ]; then
  printf '\033[32m%d passaram, 0 falharam.\033[0m M4 validado.\n\n' "$PASS"
  exit 0
else
  printf '\033[31m%d passaram, %d falharam.\033[0m\n\n' "$PASS" "$FAIL"
  exit 1
fi
