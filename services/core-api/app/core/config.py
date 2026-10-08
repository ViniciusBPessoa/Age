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

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
