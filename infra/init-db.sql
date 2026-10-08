-- Age — inicialização do banco
-- Roda uma única vez, na primeira subida do container (volume vazio).
-- ARQUITETURA.md §2.3.2 (schemas) e §11.2 (roles e GRANTs).
--
-- A separação tem DUAS dimensões que não devem ser confundidas:
--   1. quem é dono da estrutura  -> age_owner, roda as migrations
--   2. quem lê os dados          -> core_api / ai_worker, runtime
--
-- Misturar as duas desliga o RLS sem ninguém notar: dono de tabela
-- ignora política por padrão (§4.2.1). É por isso que as roles de
-- runtime NÃO são donas de nada.

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------
-- Extensões
-- ---------------------------------------------------------------
-- Num schema próprio, não em `public`. Motivo concreto: mais abaixo
-- revogamos USAGE em `public` (vetor conhecido de abuso), e sem USAGE
-- nenhuma role consegue RESOLVER os tipos da extensão — o erro aparece
-- como `type "citext" does not exist`, que não parece problema de
-- permissão e custa tempo para diagnosticar.
CREATE SCHEMA IF NOT EXISTS ext;
CREATE EXTENSION IF NOT EXISTS "pgcrypto" SCHEMA ext;  -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "citext"   SCHEMA ext;  -- e-mail case-insensitive

-- ---------------------------------------------------------------
-- Roles
-- ---------------------------------------------------------------
-- age_owner: dono dos schemas. As migrations rodam com ele.
-- O superusuário do container (POSTGRES_USER) já existe; criamos
-- age_owner separado para que nem as migrations rodem como super.
CREATE ROLE age_owner LOGIN PASSWORD :'owner_password';

-- Runtime: NOBYPASSRLS é o que garante que a política de §4.2 valha.
CREATE ROLE core_api  LOGIN PASSWORD :'core_api_password'  NOBYPASSRLS;
CREATE ROLE ai_worker LOGIN PASSWORD :'ai_worker_password' NOBYPASSRLS;

-- ---------------------------------------------------------------
-- Schemas
-- ---------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS core AUTHORIZATION age_owner;
CREATE SCHEMA IF NOT EXISTS ai   AUTHORIZATION age_owner;

-- public não serve a nada aqui e é vetor conhecido de abuso
REVOKE ALL ON SCHEMA public FROM PUBLIC;

-- ---------------------------------------------------------------
-- GRANTs de runtime
-- ---------------------------------------------------------------
-- core-api: escreve em `core`, lê `ai`
GRANT USAGE ON SCHEMA core, ai TO core_api;

-- ai-worker: escreve em `ai`, lê `core`
GRANT USAGE ON SCHEMA ai, core TO ai_worker;

-- Todos precisam resolver os tipos e funções das extensões.
GRANT USAGE ON SCHEMA ext TO age_owner, core_api, ai_worker;

-- Nenhuma role de runtime cria objeto: estrutura é só de migration.
REVOKE CREATE ON SCHEMA core FROM core_api, ai_worker;
REVOKE CREATE ON SCHEMA ai   FROM core_api, ai_worker;

-- ---------------------------------------------------------------
-- DEFAULT PRIVILEGES
-- ---------------------------------------------------------------
-- O `FOR ROLE age_owner` é OBRIGATÓRIO: sem ele, o default se aplica a
-- quem rodou este script (o superusuário), e as tabelas criadas depois
-- pelas migrations (como age_owner) não herdariam permissão nenhuma.
ALTER DEFAULT PRIVILEGES FOR ROLE age_owner IN SCHEMA core
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO core_api;
ALTER DEFAULT PRIVILEGES FOR ROLE age_owner IN SCHEMA core
  GRANT SELECT ON TABLES TO ai_worker;
ALTER DEFAULT PRIVILEGES FOR ROLE age_owner IN SCHEMA core
  GRANT USAGE ON SEQUENCES TO core_api;

ALTER DEFAULT PRIVILEGES FOR ROLE age_owner IN SCHEMA ai
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ai_worker;
ALTER DEFAULT PRIVILEGES FOR ROLE age_owner IN SCHEMA ai
  GRANT SELECT ON TABLES TO core_api;
ALTER DEFAULT PRIVILEGES FOR ROLE age_owner IN SCHEMA ai
  GRANT USAGE ON SEQUENCES TO ai_worker;

-- ---------------------------------------------------------------
-- search_path por role: evita qualificar schema em toda query
-- ---------------------------------------------------------------
-- `ext` entra no path para que `citext` e `gen_random_uuid()` resolvam
-- sem qualificação. `public` fica de fora: não guarda nada nosso.
ALTER ROLE core_api  SET search_path = core, ai, ext;
ALTER ROLE ai_worker SET search_path = ai, core, ext;
ALTER ROLE age_owner SET search_path = core, ai, ext;
