"""Dependências do FastAPI: usuário autenticado e sessão de banco.

ARQUITETURA.md §4.1.1 e §4.2.2.

A combinação `CurrentUser` + `UserSession` é o único caminho para chegar
aos dados: a sessão de banco só é criada depois de haver `user_id`
validado, e ela seta `app.user_id` na transação. Isso torna
estruturalmente difícil escrever uma rota que esqueça o RLS.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import user_session
from app.core.security import TokenError, decode_access_token
from app.core.tracing import set_user_id as set_log_user_id


@dataclass(frozen=True)
class AuthenticatedUser:
    id: UUID
    session_id: UUID
    role: str


def _unauthorized(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": {"code": code, "message": message}},
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(request: Request) -> AuthenticatedUser:
    """Extrai e valida o JWT de acesso do header.

    O BFF é quem monta este header, lendo o cookie httpOnly (§4.1.1).
    O JavaScript do navegador nunca vê o token.
    """
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise _unauthorized("missing_token", "Autenticação necessária.")

    try:
        payload = decode_access_token(header.removeprefix("Bearer ").strip())
    except TokenError as exc:
        # `expired` é distinto de `invalid` porque o BFF reage diferente:
        # expirado dispara a renovação silenciosa (§4.1.2).
        code = "token_expired" if "expirado" in str(exc) else "invalid_token"
        raise _unauthorized(code, str(exc)) from exc

    # §12 pede `user_id` no log estruturado, e este é o único ponto por
    # onde todo request autenticado passa — pôr aqui é o que garante que
    # nenhuma rota nova esqueça. Vale para o request inteiro: o
    # `ContextVar` é zerado por `TraceMiddleware` a cada requisição.
    set_log_user_id(str(payload["sub"]))

    return AuthenticatedUser(
        id=UUID(payload["sub"]),
        session_id=UUID(payload["sid"]),
        role=payload.get("role", "user"),
    )


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


async def get_db(user: CurrentUser) -> AsyncIterator[AsyncSession]:
    """Sessão de banco com `app.user_id` já setado.

    Depende de `CurrentUser` de propósito: não existe forma de obter
    sessão sem usuário autenticado por este caminho.

    **Também confere se a conta e a sessão ainda valem**, e isso não é
    zelo excessivo. O JWT de acesso é validado só criptograficamente
    (`get_current_user`), que é a troca normal de token stateless — mas
    significa que um token emitido antes de a conta ser encerrada
    continua abrindo tudo pelos 15 minutos restantes.

    Foi exatamente o que aconteceu: depois de `POST
    /privacy/delete-account`, com a conta marcada e o refresh revogado,
    o access token antigo seguia devolvendo 200. O refresh não renova,
    então a janela é curta — mas "curta" não é "nenhuma", e quem acabou
    de encerrar a conta não espera que o acesso continue.

    A checagem roda aqui, e não em `get_current_user`, porque aqui a
    conexão com o banco já existe: o custo é uma consulta por índice
    primário na transação que ia abrir de todo jeito. Pôr em
    `get_current_user` exigiria abrir uma sessão só para isso.
    """
    async with user_session(user.id) as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT u.deleted_at, s.revoked_at
                    FROM core.users u
                    LEFT JOIN core.sessions s
                           ON s.id = :sid AND s.user_id = u.id
                    WHERE u.id = :uid
                    """
                ),
                {"uid": str(user.id), "sid": str(user.session_id)},
            )
        ).first()

        if row is None or row[0] is not None:
            raise _unauthorized(
                "account_deleted",
                "Esta conta foi encerrada. Entre de novo para reativar, "
                "se ainda estiver na janela de 30 dias.",
            )
        if row[1] is not None:
            raise _unauthorized(
                "session_revoked",
                "Esta sessão foi revogada. Entre de novo.",
            )

        yield session


DbSession = Annotated[AsyncSession, Depends(get_db)]


async def require_admin(user: CurrentUser) -> AuthenticatedUser:
    """Admin vê métricas agregadas, nunca conteúdo de ninguém (§4.4)."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "forbidden",
                    "message": "Esta ação requer privilégio de administrador.",
                }
            },
        )
    return user


AdminUser = Annotated[AuthenticatedUser, Depends(require_admin)]


def client_ip(request: Request) -> str | None:
    """IP do cliente, respeitando o proxy.

    `X-Forwarded-For` acumula saltos; o primeiro é o cliente original.
    Confiamos nele porque o único proxy no caminho é o nosso (§11.1.1).
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
