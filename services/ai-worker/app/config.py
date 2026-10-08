"""Configuração do ai-worker."""

from functools import lru_cache

from pydantic import Field, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    service_name: str = "ai-worker"
    environment: str = Field(default="development")

    database_url: str
    db_schema_own: str = "ai"
    db_schema_read: str = "core"
    db_pool_size: int = 5
    db_echo: bool = False

    redis_url: RedisDsn

    # O worker LE a agenda pelo core-api, que e o dono dela (secao 3.4).
    core_api_internal_url: str = "http://core-api:8000"

    master_key: str = Field(min_length=32)
    service_jwt_secret: str = Field(min_length=32)

    # Quantos jobs em paralelo. Jobs de LLM são I/O-bound, então um valor
    # baixo já aproveita bem; o limite real é a cota do provedor.
    worker_concurrency: int = 4

    # Guarda contra turno pendurado (§7.1). O worker não vê o navegador,
    # então sem isto um job travado consome cota para sempre.
    turn_timeout_seconds: int = 300
    turn_stream_ttl_seconds: int = 3600

    # Orçamento do laço do orquestrador (§5.1).
    max_tool_iterations: int = 8


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
