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

    # --- Chave de instância (§3451) ---
    # A saída que a própria especificação previu para o atrito da chave
    # de API: "um modo opcional em que o operador fornece a chave com
    # cota por usuário — o esquema já suporta, mas aí o custo passa a
    # ser seu".
    #
    # Vive em variável de ambiente, e NÃO como uma linha de
    # `core.llm_credentials` com um `user_id` inventado. Duas razões: a
    # tabela é protegida por RLS com política `user_id = app.user_id`,
    # então uma credencial que não é de ninguém não tem como ser lida
    # sem furar a política — e furar o RLS é exatamente o que o projeto
    # inteiro evita (§4.2.2). A segunda é operacional: rotacionar a
    # chave do operador passa a ser trocar uma variável e reiniciar, sem
    # UPDATE em dado de usuário.
    #
    # Vazia = o comportamento anterior, cada usuário com a sua chave.
    instance_llm_api_key: str = ""
    instance_llm_provider: str = "deepseek"
    instance_llm_model: str = ""
    instance_llm_base_url: str = ""

    @property
    def has_instance_key(self) -> bool:
        return bool(self.instance_llm_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
