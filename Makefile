# Age — comandos de desenvolvimento
# ARQUITETURA.md §11.4

# --env-file: o .env está na raiz e o compose em infra/ — o Docker só
# carrega .env automaticamente do diretório do arquivo compose.
COMPOSE := docker compose -f infra/docker-compose.yml --env-file .env
PY_SERVICES := core-api ai-worker

.DEFAULT_GOAL := help
.PHONY: help env up down logs ps migrate migration gen-types test \
        test-contract lint fmt shell-db shell-redis reset smoke

help: ## Lista os comandos
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

env: ## Gera .env com segredos aleatórios (não sobrescreve existente)
	@if [ -f .env ]; then \
	  echo ".env já existe — apague antes se quiser regerar."; exit 1; \
	fi
	@cp .env.example .env
	@for key in POSTGRES_PASSWORD AGE_OWNER_PASSWORD CORE_API_DB_PASSWORD \
	            AI_WORKER_DB_PASSWORD REDIS_PASSWORD MASTER_KEY \
	            JWT_SIGNING_KEY SERVICE_JWT_SECRET; do \
	  secret=$$(openssl rand -hex 24); \
	  sed -i.bak "s|^$$key=.*|$$key=$$secret|" .env; \
	done
	@rm -f .env.bak
	@echo ".env gerado com segredos aleatórios."
	@echo "AVISO: perder MASTER_KEY = perder as credenciais salvas."

up: ## Sobe tudo e aplica as migrations
	$(COMPOSE) up -d --build
	@echo "aguardando core-api ficar pronto…"
	@until $(COMPOSE) ps core-api | grep -q healthy; do sleep 2; done
	@$(MAKE) migrate
	@echo ""
	@echo "  web       http://localhost:3000"
	@echo "  core-api  http://localhost:8000/docs"
	@echo ""

down: ## Para os serviços (mantém os dados)
	$(COMPOSE) down

ps: ## Estado dos containers
	$(COMPOSE) ps

logs: ## Segue os logs dos três serviços
	$(COMPOSE) logs -f core-api ai-worker ai-worker-api web

migrate: ## Aplica as migrations dos dois schemas, na ordem
	@echo "→ migrando schema core"
	$(COMPOSE) exec -T core-api alembic upgrade head
	@echo "→ migrando schema ai"
	$(COMPOSE) exec -T ai-worker alembic upgrade head

migration: ## Cria migration no core-api: make migration m="descrição"
	@test -n "$(m)" || (echo 'uso: make migration m="descrição"'; exit 1)
	$(COMPOSE) exec core-api alembic revision --autogenerate -m "$(m)"

gen-types: ## OpenAPI do core-api -> tipos TypeScript (§2.5)
	@mkdir -p packages/age-types
	@curl -sf localhost:8000/openapi.json -o /tmp/age-openapi.json \
	  || (echo "core-api não responde — rode make up"; exit 1)
	@cd services/web && npx --yes openapi-typescript \
	  /tmp/age-openapi.json -o ../../packages/age-types/api.ts
	@echo "tipos gerados em packages/age-types/api.ts"

test: ## Suíte completa
	$(COMPOSE) exec -T core-api pytest -q
	$(COMPOSE) exec -T ai-worker pytest -q

test-contract: ## Só as suítes de contrato das portas (§10.1)
	$(COMPOSE) exec -T core-api pytest -q tests/contract
	$(COMPOSE) exec -T ai-worker pytest -q tests/contract

lint: ## Ruff + tsc
	$(COMPOSE) exec -T core-api ruff check .
	$(COMPOSE) exec -T ai-worker ruff check .
	cd services/web && npm run typecheck

fmt: ## Formata
	$(COMPOSE) exec -T core-api ruff format .
	$(COMPOSE) exec -T ai-worker ruff format .

shell-db: ## psql como age_owner
	$(COMPOSE) exec postgres psql -U age_super -d age

shell-redis: ## redis-cli autenticado
	$(COMPOSE) exec redis sh -c 'redis-cli -a "$$REDIS_PASSWORD"'

smoke: ## Valida o M0 ponta a ponta
	@bash infra/smoke.sh

reset: ## APAGA os volumes e recomeça do zero
	@printf "Isto apaga o banco e o Redis. Confirma? [y/N] " && read ans && [ "$$ans" = "y" ]
	$(COMPOSE) down -v
	@echo "volumes removidos. rode make up"
