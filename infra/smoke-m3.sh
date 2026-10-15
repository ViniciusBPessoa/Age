#!/usr/bin/env bash
# Age — validação do M3 (estudo).
#
# Critério de pronto (PROGRESSO.md): "preciso estudar Álgebra Linear,
# prova dia 20" gera sessões distribuídas, e marcar uma como pulada
# reagenda.
#
# O foco é no comportamento honesto: respeitar pré-requisito, nunca
# agendar no dia da prova, reportar o que não couber, e recalibrar a
# estimativa pelo tempo real.
set -uo pipefail

API="http://localhost:8000/api/v1"
PASS=0
FAIL=0
EMAIL="smoke-m3-$RANDOM@exemplo.com"
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

# Tópicos de Álgebra Linear, com os pré-requisitos reais da disciplina.
TOPICS='[
  {"name":"Sistemas lineares","estimated_min":90},
  {"name":"Espacos vetoriais","estimated_min":120,"prerequisites":["Sistemas lineares"]},
  {"name":"Base e dimensao","estimated_min":120,"prerequisites":["Espacos vetoriais"]},
  {"name":"Transformacoes lineares","estimated_min":90,"prerequisites":["Base e dimensao"]},
  {"name":"Autovalores","estimated_min":120,"prerequisites":["Transformacoes lineares"]},
  {"name":"Revisao e exercicios","estimated_min":120}
]'

# ---------------------------------------------------------------
head_ "1. Conta e preferências"

TOKEN=$(curl -s -X POST "$API/auth/register" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$SENHA\"}" | jq_ "d['access_token']")

if [ -z "$TOKEN" ]; then
  bad "registro falhou"
  printf '\n\033[31mM3 incompleto.\033[0m\n\n'; exit 1
fi
AUTH=(-H "Authorization: Bearer $TOKEN")
ok "conta criada"

PREFS=$(curl -s -X PUT "$API/preferences" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d '{"preferred_study_times":[["14:00","18:00"]],"max_hours_per_day":4,
       "session_min":50,"session_max":90}')
if [ "$(echo "$PREFS" | jq_ "d['max_hours_per_day']")" = "4" ]; then
  ok "preferências de estudo salvas"
else
  bad "falha ao salvar preferências: $(echo "$PREFS" | head -c 150)"
fi

# ---------------------------------------------------------------
head_ "2. Decomposição e alocação (§5.5)"

PLAN=$(curl -s -X POST "$API/study/plan/preview" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d "{\"subject\":\"Algebra Linear\",
       \"deadline\":\"2028-05-20T14:00:00-03:00\",
       \"start_from\":\"2028-05-05T08:00:00-03:00\",
       \"topics\":$TOPICS}")

if [ "$(echo "$PLAN" | jq_ "d['fits']")" = "True" ]; then
  ok "11h de estudo caberam na janela"
else
  bad "não caberam: $(echo "$PLAN" | jq_ "d['unplaced']")"
fi

HOURS=$(echo "$PLAN" | jq_ "d['total_hours']")
if [ "$HOURS" = "11.0" ]; then
  ok "total agendado bate com o estimado (11h)"
else
  bad "total agendado: ${HOURS}h (esperado 11.0)"
fi

DAYS=$(echo "$PLAN" | jq_ "d['days_used']")
if [ "${DAYS:-0}" -ge 5 ]; then
  ok "sessões distribuídas em $DAYS dias"
else
  bad "concentradas em $DAYS dias — maratona, não plano de estudo"
fi

# Pré-requisito é regra dura: autovalores depois de espaços vetoriais.
ORDER_OK=$(echo "$PLAN" | python -c "
import json,sys
d = json.load(sys.stdin)
study = [s for s in d['sessions'] if s['kind'] == 'study']
first = {}
for s in study:
    first.setdefault(s['topic'], s['start'])
pairs = [
    ('Sistemas lineares', 'Espacos vetoriais'),
    ('Espacos vetoriais', 'Base e dimensao'),
    ('Base e dimensao', 'Transformacoes lineares'),
    ('Transformacoes lineares', 'Autovalores'),
]
bad = [f'{a}>{b}' for a, b in pairs
       if a in first and b in first and first[a] >= first[b]]
print(','.join(bad) if bad else 'ok')
" 2>/dev/null)
if [ "$ORDER_OK" = "ok" ]; then
  ok "pré-requisitos respeitados na ordem temporal"
else
  bad "ordem violada: $ORDER_OK"
fi

# Nunca no dia da prova (§5.5).
ON_EXAM=$(echo "$PLAN" | python -c "
import json,sys
d=json.load(sys.stdin)
print(len([s for s in d['sessions'] if s['start'][:10] == '2028-05-20']))
" 2>/dev/null)
if [ "${ON_EXAM:-1}" = "0" ]; then
  ok "nada agendado no dia da prova"
else
  bad "$ON_EXAM sessões no dia da prova"
fi

# Horário preferido respeitado, e a justificativa diz isso.
PREFERRED=$(echo "$PLAN" | python -c "
import json,sys
d=json.load(sys.stdin)
study=[s for s in d['sessions'] if s['kind']=='study']
print(len([s for s in study if 'preferido' in s['reason']]))
" 2>/dev/null)
if [ "${PREFERRED:-0}" -ge 1 ]; then
  ok "horário preferido usado e justificado"
else
  bad "preferência de horário ignorada"
fi

# Toda sessão tem justificativa (§5.6).
NO_REASON=$(echo "$PLAN" | python -c "
import json,sys
d=json.load(sys.stdin)
print(len([s for s in d['sessions'] if not s['reason'].strip()]))
" 2>/dev/null)
if [ "${NO_REASON:-1}" = "0" ]; then
  ok "toda sessão tem justificativa"
else
  bad "$NO_REASON sessões sem reason"
fi

# Revisão espaçada presente e curta.
REVIEWS=$(echo "$PLAN" | python -c "
import json,sys
d=json.load(sys.stdin)
r=[s for s in d['sessions'] if s['kind']=='review']
print(len(r), max([s['duration_min'] for s in r], default=0))
" 2>/dev/null)
set -- $REVIEWS
if [ "${1:-0}" -ge 1 ]; then
  ok "revisão espaçada incluída (${1} sessões)"
else
  bad "nenhuma revisão espaçada"
fi
if [ "${2:-999}" -le 30 ]; then
  ok "revisões são curtas (${2}min) — relembrar custa menos"
else
  bad "revisões longas demais: ${2}min"
fi

# ---------------------------------------------------------------
head_ "3. Caso \"não cabe\" (§5.5 passo 6)"

# 11h numa janela de 1 dia com limite de 4h/dia.
TIGHT=$(curl -s -X POST "$API/study/plan/preview" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d "{\"subject\":\"Impossivel\",
       \"deadline\":\"2028-06-02T08:00:00-03:00\",
       \"start_from\":\"2028-06-01T08:00:00-03:00\",
       \"include_reviews\":false,
       \"topics\":$TOPICS}")

if [ "$(echo "$TIGHT" | jq_ "d['fits']")" = "False" ]; then
  ok "plano impossível é reportado como tal"
else
  bad "plano impossível reportou fits=true"
fi

SHORTFALL=$(echo "$TIGHT" | jq_ "d['shortfall']")
if echo "$SHORTFALL" | grep -q "Não cabe tudo"; then
  ok "mensagem de falta é explícita"
else
  bad "mensagem de falta ausente"
fi
# Oferece as três saídas reais, não "não consegui".
for opcao in "estender o prazo" "reduzir a profundidade" "bloqueou"; do
  if echo "$SHORTFALL" | grep -q "$opcao"; then
    ok "oferece a opção: $opcao"
  else
    bad "não oferece: $opcao"
  fi
done

# ---------------------------------------------------------------
head_ "4. Materialização e acompanhamento (§5.9.8)"

CREATED=$(curl -s -X POST "$API/study/subjects" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d "{\"subject\":\"Algebra Linear\",
       \"deadline\":\"2028-05-20T14:00:00-03:00\",
       \"start_from\":\"2028-05-05T08:00:00-03:00\",
       \"include_reviews\":false,
       \"topics\":$TOPICS}")
SUBJECT_ID=$(echo "$CREATED" | jq_ "d['subject_id']")

if [ -n "$SUBJECT_ID" ]; then
  ok "assunto materializado ($(echo "$CREATED" | jq_ "d['sessions_created']") sessões)"
else
  bad "materialização falhou: $(echo "$CREATED" | head -c 200)"
  printf '\n\033[31mM3 incompleto.\033[0m\n\n'; exit 1
fi

PROGRESS=$(curl -s "$API/study/subjects/$SUBJECT_ID" "${AUTH[@]}")
if [ "$(echo "$PROGRESS" | jq_ "d['sessions_done']")" = "0" ]; then
  ok "progresso começa em zero"
else
  bad "progresso inicial errado"
fi
if [ "$(echo "$PROGRESS" | jq_ "d['topics_total']")" = "6" ]; then
  ok "6 tópicos registrados"
else
  bad "tópicos: $(echo "$PROGRESS" | jq_ "d['topics_total']")"
fi

# Empurra as sessões para o passado, para virarem "pendentes".
psql_ "UPDATE core.study_sessions s
       SET scheduled_start = now() - interval '2 days',
           scheduled_end = now() - interval '2 days' + interval '60 minutes'
       WHERE s.topic_id IN (
         SELECT id FROM core.study_topics WHERE subject_id = '$SUBJECT_ID'
       );" >/dev/null

PENDING=$(curl -s "$API/study/sessions/pending" "${AUTH[@]}")
PENDING_COUNT=$(echo "$PENDING" | python -c "
import json,sys; print(len(json.load(sys.stdin)))
" 2>/dev/null)
if [ "${PENDING_COUNT:-0}" -ge 1 ]; then
  ok "$PENDING_COUNT sessões aparecem para marcação rápida (§8.1)"
else
  bad "nenhuma sessão pendente"
fi

# ---------------------------------------------------------------
head_ "5. Recalibração de estimativa (§5.9.8)"

# Duas sessões do MESMO tópico, concluídas mais rápido que o planejado.
TOPIC_ID=$(psql_ "SELECT id FROM core.study_topics
                  WHERE subject_id = '$SUBJECT_ID'
                    AND name LIKE 'Espacos%' LIMIT 1;" | tr -d '[:space:]')
SESSIONS=$(psql_ "SELECT id FROM core.study_sessions
                  WHERE topic_id = '$TOPIC_ID' LIMIT 2;" | tr -d ' ')

LAST_CAL=""
for sid in $SESSIONS; do
  [ -z "$sid" ] && continue
  LAST_CAL=$(curl -s -X POST "$API/study/sessions/$sid/mark" "${AUTH[@]}" \
    -H 'Content-Type: application/json' \
    -d '{"status":"done","actual_min":40}')
done

ORIGINAL=$(echo "$LAST_CAL" | python -c "
import json,sys
d=json.load(sys.stdin); c=d.get('calibration') or {}
print(c.get('estimated_min',''))
" 2>/dev/null)
CALIBRATED=$(echo "$LAST_CAL" | python -c "
import json,sys
d=json.load(sys.stdin); c=d.get('calibration') or {}
print(c.get('calibrated_min',''))
" 2>/dev/null)

if [ -n "$CALIBRATED" ]; then
  ok "estimativa recalibrada: ${ORIGINAL}min → ${CALIBRATED}min"
else
  bad "recalibração não ocorreu: $(echo "$LAST_CAL" | head -c 200)"
fi

# A aritmética tem de fazer sentido. O bug real que isto pega: o
# parâmetro fracionário era truncado e 120min viravam 15min.
if [ -n "$CALIBRATED" ] && [ -n "$ORIGINAL" ]; then
  SANE=$(python -c "
o, c = $ORIGINAL, $CALIBRATED
# Estudou 2/3 do tempo: o ajuste deve ficar entre 60% e 100% do original.
print('ok' if o * 0.6 <= c <= o else f'absurdo: {c} de {o}')
" 2>/dev/null)
  if [ "$SANE" = "ok" ]; then
    ok "valor recalibrado é proporcional, não truncado"
  else
    bad "recalibração $SANE"
  fi
fi

DIRECTION=$(echo "$LAST_CAL" | python -c "
import json,sys
d=json.load(sys.stdin); c=d.get('calibration') or {}
print(c.get('direction',''))
" 2>/dev/null)
if [ "$DIRECTION" = "faster" ]; then
  ok "direção classificada como 'faster'"
else
  bad "direção: '$DIRECTION' (esperado faster)"
fi

AFTER=$(curl -s "$API/study/subjects/$SUBJECT_ID" "${AUTH[@]}")
if [ "$(echo "$AFTER" | jq_ "d['sessions_done']")" -ge 2 ] 2>/dev/null; then
  ok "sessões concluídas contabilizadas"
else
  bad "contagem de concluídas errada"
fi

# ---------------------------------------------------------------
head_ "6. Pular sessão e o gatilho de 3 faltas (§5.9.8)"

# `s.id` qualificado: sem o alias, `id` e ambiguo no JOIN e o psql
# devolve a mensagem de erro, que ia direto para a URL do curl.
SKIP_IDS=$(psql_ "SELECT s.id FROM core.study_sessions s
                  JOIN core.study_topics t ON t.id = s.topic_id
                  WHERE t.subject_id = '$SUBJECT_ID'
                    AND s.status = 'planned' LIMIT 3;" | tr -d ' ')

LAST_SKIP=""
for sid in $SKIP_IDS; do
  [ -z "$sid" ] && continue
  LAST_SKIP=$(curl -s -X POST "$API/study/sessions/$sid/mark" "${AUTH[@]}" \
    -H 'Content-Type: application/json' -d '{"status":"skipped"}')
done

STREAK=$(echo "$LAST_SKIP" | jq_ "d['skip_streak']")
if [ "${STREAK:-0}" -ge 3 ]; then
  ok "sequência de faltas contada ($STREAK)"
else
  bad "sequência de faltas: ${STREAK:-?} (esperado >= 3)"
fi

if [ "$(echo "$LAST_SKIP" | jq_ "d['should_rethink']")" = "True" ]; then
  ok "gatilho de repensar o plano acionado"
else
  bad "gatilho de 3 faltas não acionou"
fi

# ---------------------------------------------------------------
head_ "7. Preferências aprendidas (§5.9.7)"

curl -s -o /dev/null -X PUT "$API/study/preferences/learned" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d '{"key":"no_study_before","value":"14:00","confirmed":true}'

LEARNED=$(curl -s "$API/study/preferences/learned" "${AUTH[@]}")
if echo "$LEARNED" | grep -q "no_study_before"; then
  ok "preferência aprendida salva"
else
  bad "preferência não foi salva"
fi

# E ela deve AFETAR o próximo plano: nada antes das 14h.
AFTER_PREF=$(curl -s -X POST "$API/study/plan/preview" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d '{"subject":"Teste","deadline":"2028-07-20T14:00:00-03:00",
       "start_from":"2028-07-01T08:00:00-03:00","include_reviews":false,
       "topics":[{"name":"Topico","estimated_min":60}]}')
EARLY=$(echo "$AFTER_PREF" | python -c "
import json,sys
d=json.load(sys.stdin)
print(len([s for s in d['sessions'] if s['start'][11:16] < '14:00']))
" 2>/dev/null)
if [ "${EARLY:-1}" = "0" ]; then
  ok "preferência aprendida aplicada ao novo plano"
else
  bad "$EARLY sessões antes das 14h, contrariando a preferência"
fi

# ---------------------------------------------------------------
head_ "8. Isolamento (§4.2)"

OTHER=$(curl -s -X POST "$API/auth/register" -H 'Content-Type: application/json' \
  -d "{\"email\":\"outro-m3-$RANDOM@exemplo.com\",\"password\":\"$SENHA\"}" \
  | jq_ "d['access_token']")

PEEK=$(curl -s -o /dev/null -w "%{http_code}" "$API/study/subjects/$SUBJECT_ID" \
  -H "Authorization: Bearer $OTHER")
if [ "$PEEK" = "404" ]; then
  ok "outra conta não acessa assunto alheio"
else
  bad "VAZAMENTO: assunto alheio retornou $PEEK"
fi

OTHER_SUBJECTS=$(curl -s "$API/study/subjects" -H "Authorization: Bearer $OTHER" \
  | python -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null)
if [ "${OTHER_SUBJECTS:-1}" = "0" ]; then
  ok "conta nova vê zero assuntos"
else
  bad "conta nova viu $OTHER_SUBJECTS assuntos"
fi

# ---------------------------------------------------------------
head_ "Resultado"
printf '  %d passaram, %d falharam\n\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] && printf '\033[32mM3 validado.\033[0m\n\n' && exit 0
printf '\033[31mM3 incompleto.\033[0m\n\n' && exit 1
