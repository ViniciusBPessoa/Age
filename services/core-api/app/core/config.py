"""Configuração do core-api, lida do ambiente.

Variáveis sem default são obrigatórias: o serviço falha ao subir se
faltarem, em vez de rodar com MASTER_KEY vazia e só descobrir quando a
primeira credencial for cifrada (ARQUITETURA.md §11.1).
"""

from functools import lru_cache

from pydantic import Field, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    service_name: str = "core-api"
    environment: str = Field(default="development")

    # --- Banco ---
    database_url: str
    db_schema_own: str = "core"
    db_schema_read: str = "ai"
    db_pool_size: int = 10
    db_echo: bool = False

    # --- Redis: fila + stream de turno ---
    redis_url: RedisDsn

    # --- Segredos (§4.3) ---
    master_key: str = Field(min_length=32)
    jwt_signing_key: str = Field(min_length=32)
    service_jwt_secret: str = Field(min_length=32)

    # --- Tokens (§4.1.1) ---
    access_token_ttl_seconds: int = 15 * 60
    refresh_token_ttl_seconds: int = 30 * 24 * 60 * 60

    # --- Serviços vizinhos ---
    ai_worker_internal_url: str = "http://ai-worker:8001"
    public_base_url: str = "http://localhost:3000"

    # --- Stream de turno (§7.1) ---
    turn_stream_ttl_seconds: int = 3600

    # --- Rate limit (§2219, §9.1) ---
    # Configurável porque o número certo depende do ambiente: o smoke
    # test faz dezenas de logins seguidos de um IP só, e CI roda tudo
    # atrás de um NAT. Em vez de o teste contornar o limite, ele o
    # ajusta — o caminho exercitado continua sendo o de produção.
    #
    # Os defaults valem para produção. Login: 5 tentativas por e-mail em
    # 15 min é folgado para quem erra a senha e estreito para quem
    # chuta. O limite por IP é mais alto porque uma casa com NAT
    # compartilha IP, e seria injusto bloquear a família porque alguém
    # errou a senha três vezes.
    rate_limit_enabled: bool = True
    rate_limit_login_per_email: str = "5/900"
    rate_limit_login_per_ip: str = "20/900"
    # 20/hora, e não 5: um IP legítimo é compartilhado. Uma república,
    # um laboratório da faculdade ou um escritório atrás de NAT criam
    # várias contas no mesmo dia, e 5 bloqueia o uso real enquanto 20
    # ainda contém o laço automatizado (que faria milhares). A bateria
    # de smoke cria ~11 contas do mesmo IP, e foi ela que mostrou que o
    # número de partida estava errado.
    rate_limit_register_per_ip: str = "20/3600"
    rate_limit_refresh_per_ip: str = "60/300"
    # Mais baixo, e por usuário: cada chamada aqui gasta token de uma
    # chave de API de verdade (§2219).
    rate_limit_llm_per_user: str = "30/60"
    rate_limit_llm_verify_per_user: str = "10/300"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
