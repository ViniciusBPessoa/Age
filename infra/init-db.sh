#!/bin/bash
# Age — wrapper de inicialização do banco.
#
# O entrypoint do Postgres executa os arquivos de
# /docker-entrypoint-initdb.d em ordem alfabética. Este .sh roda antes do
# .sql e passa as senhas como variáveis do psql, para que não fiquem
# escritas em arquivo versionado.
#
# Roda UMA VEZ, quando o volume de dados está vazio. Para reexecutar:
#   make reset
set -euo pipefail

: "${AGE_OWNER_PASSWORD:?AGE_OWNER_PASSWORD obrigatório}"
: "${CORE_API_DB_PASSWORD:?CORE_API_DB_PASSWORD obrigatório}"
: "${AI_WORKER_DB_PASSWORD:?AI_WORKER_DB_PASSWORD obrigatório}"

echo "[init-db] criando roles e schemas…"

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     -v "owner_password=$AGE_OWNER_PASSWORD" \
     -v "core_api_password=$CORE_API_DB_PASSWORD" \
     -v "ai_worker_password=$AI_WORKER_DB_PASSWORD" \
     -f /age/init-db.sql

echo "[init-db] pronto."
