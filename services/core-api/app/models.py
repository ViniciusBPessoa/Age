"""Modelos ORM do schema `core`.

Mapeiam as tabelas das migrations. Ficam num arquivo só porque são
poucos e sempre lidos juntos — dividir agora seria organização sem
ganho.

Atenção: estes modelos NÃO são as entidades de domínio. O domínio
(`app/domain/`) usa dataclasses puras, sem SQLAlchemy — é a invariante
verificada em §10.2.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "core"}

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text, default="America/Sao_Paulo")
    locale: Mapped[str] = mapped_column(Text, default="pt-BR")
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    role: Mapped[str] = mapped_column(Text, default="user")
    onboarding_step: Mapped[int] = mapped_column(Integer, default=0)
    # Cota mensal do modo de chave de instância, em USD (§3451, migration
    # 0010). NULL = ilimitado, que é o caso de quem usa a própria chave e
    # das contas liberadas pelo operador. `Numeric` e não float porque é
    # dinheiro, e a comparação "usado >= limite" decide se a pessoa
    # consegue mandar mensagem.
    monthly_quota_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = {"schema": "core"}

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("core.users.id", ondelete="CASCADE")
    )
    refresh_token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    token_family_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    device_label: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthToken(Base):
    __tablename__ = "auth_tokens"
    __table_args__ = {"schema": "core"}

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("core.users.id", ondelete="CASCADE")
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class OAuthIdentity(Base):
    __tablename__ = "oauth_identities"
    __table_args__ = {"schema": "core"}

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("core.users.id", ondelete="CASCADE")
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class LLMCredential(Base):
    __tablename__ = "llm_credentials"
    __table_args__ = {"schema": "core"}

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("core.users.id", ondelete="CASCADE")
    )
    provider_key: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_api_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_last4: Mapped[str] = mapped_column(Text, nullable=False)
    default_model: Mapped[str | None] = mapped_column(Text)
    base_url: Mapped[str | None] = mapped_column(Text)
    label: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = {"schema": "core"}

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("core.users.id", ondelete="CASCADE")
    )
    title: Mapped[str | None] = mapped_column(Text)
    title_is_auto: Mapped[bool] = mapped_column(Boolean, default=True)
    provider_key: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    target_default: Mapped[str] = mapped_column(Text, default="event")
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = {"schema": "core"}

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("core.users.id", ondelete="CASCADE")
    )
    conversation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("core.conversations.id", ondelete="CASCADE"),
    )
    # Idempotência de envio: índice único parcial no banco é o único
    # árbitro confiável para duplo-clique (migration 0005).
    client_turn_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    tool_calls: Mapped[dict | None] = mapped_column(JSON)
    tool_call_id: Mapped[str | None] = mapped_column(Text)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    # O custo do PAR (pergunta + resposta), guardado na resposta: o
    # provedor cobra o prompt inteiro, que já inclui a pergunta, então
    # é na resposta que a cobrança acontece. `None` = desconhecido.
    estimated_cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6))
    provider_key: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserPreferences(Base):
    __tablename__ = "user_preferences"
    __table_args__ = {"schema": "core"}

    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("core.users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    working_hours: Mapped[dict | None] = mapped_column(JSON)
    blocked_windows: Mapped[dict | None] = mapped_column(JSON)
    preferred_study_times: Mapped[dict | None] = mapped_column(JSON)
    session_min: Mapped[int] = mapped_column(Integer, default=50)
    session_max: Mapped[int] = mapped_column(Integer, default=90)
    break_min: Mapped[int] = mapped_column(Integer, default=10)
    max_hours_per_day: Mapped[int] = mapped_column(Integer, default=6)
    buffer_min: Mapped[int] = mapped_column(Integer, default=15)
    autonomy_level: Mapped[str] = mapped_column(Text, default="propose")
    spaced_repetition_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    scoring_weights: Mapped[dict | None] = mapped_column(JSON)
    default_write_calendar_id: Mapped[str | None] = mapped_column(Text)
    default_target: Mapped[str] = mapped_column(Text, default="event")
    private_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    # Se valores em dinheiro aparecem para esta conta. O teto é do
    # operador (`cost_display_enabled`): a preferência só desliga o que
    # ele deixou ligado.
    show_cost_usd: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class UsageLog(Base):
    __tablename__ = "usage_log"
    __table_args__ = {"schema": "core"}

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("core.users.id", ondelete="CASCADE")
    )
    # A chave natural do dreno, com unique parcial no banco. `None` nas
    # linhas anteriores à migration 0012 e no uso que não vem de turno.
    turn_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    provider_key: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MessageAttachment(Base):
    """Uma imagem anexada a uma mensagem (migration 0013).

    Os bytes ficam na linha, em `data`. A razão está no cabeçalho da
    migration: arquivo no disco não compartilha transação com a linha,
    não tem RLS e não é apagado por CASCADE — e as três coisas importam
    mais aqui do que o espaço economizado.

    `message_id` é nullable porque o anexo NASCE ANTES da mensagem: o
    upload acontece enquanto a pessoa escreve, e só o envio cria a
    mensagem. Anexo que nunca foi enviado é órfão, e o índice parcial
    `ix_attachments_orfaos` existe para a varredura achá-lo.
    """

    __tablename__ = "message_attachments"
    __table_args__ = {"schema": "core"}

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("core.users.id", ondelete="CASCADE")
    )
    message_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("core.messages.id", ondelete="CASCADE")
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("core.conversations.id", ondelete="CASCADE")
    )
    # Só para mostrar na tela. O tipo vem dos BYTES, não daqui.
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[bytes | None] = mapped_column(LargeBinary)
    # Texto do PDF, extraído no upload (migration 0014). `None` em
    # imagem — e é essa distinção que o worker usa para decidir se
    # manda como imagem ou como texto.
    extracted_text: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int | None] = mapped_column(Integer)
    # Gancho para object storage futuro; `None` enquanto `data` manda.
    storage_key: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
