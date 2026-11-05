"""Bateria de segurança do core-api, de ponta a ponta.

Contra o Postgres e o Redis de verdade, com JWT de verdade: nenhum
`dependency_overrides`. O que se testa aqui é o que um atacante com uma
conta própria e um `curl` consegue fazer, e cada caso foi reproduzido à
mão na auditoria (`SEGURANCA.md`) antes de virar teste.

Grupos:

- **Autenticação**: token forjado, expirado, de outro tipo, com outra
  chave, sem `exp`; sessão encerrada no logout.
- **Isolamento entre contas**: B não lê, não altera e não apaga o que é
  de A, e não cria evento dentro do calendário de A.
- **Rate limit**: o login por IP bloqueia com 429 e `Retry-After`.
- **Upload**: o tamanho é recusado sem ler o arquivo inteiro.
- **Superfície**: `/docs` e `/openapi.json` somem em produção.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator

import jwt
import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="precisa do Postgres e do Redis (make up)",
)

SENHA = "SenhaForte#2026"
JANELA = {"start": "2026-11-01T00:00:00Z", "end": "2026-11-30T23:59:59Z"}


@pytest.fixture(autouse=True)
async def _recursos_limpos() -> AsyncIterator[None]:
    """Engine e Redis são singletons de módulo presos ao loop do teste.

    Sem descartar entre testes, o segundo falha com "Event loop is
    closed" (o mesmo padrão de `tests/integration`).
    """
    from app.core.db import dispose_engine
    from app.core.redis import close_redis

    await dispose_engine()
    await close_redis()
    yield
    await dispose_engine()
    await close_redis()


def _cliente():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    app.dependency_overrides.clear()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _ip() -> str:
    """Um IP por teste: o limite por IP não vaza de um teste para outro."""
    n = uuid.uuid4().int
    return f"10.{n % 250}.{(n >> 8) % 250}.{(n >> 16) % 250 + 1}"


async def _conta(cliente) -> dict[str, str]:
    """Cria uma conta e devolve o header de autenticação dela."""
    email = f"seg-{uuid.uuid4().hex[:12]}@age.dev.br"
    r = await cliente.post(
        "/api/v1/auth/register",
        json={"email": email, "password": SENHA, "name": "Seg"},
        headers={"X-Forwarded-For": _ip()},
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _evento(cliente, auth: dict[str, str], titulo: str = "Consulta") -> dict:
    r = await cliente.post(
        "/api/v1/calendar/events",
        json={
            "title": titulo,
            "start": "2026-11-10T14:00:00-03:00",
            "end": "2026-11-10T15:00:00-03:00",
        },
        headers=auth,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _claims(**extra) -> dict:
    agora = int(time.time())
    base = {
        "sub": str(uuid.uuid4()),
        "sid": str(uuid.uuid4()),
        "typ": "access",
        "role": "user",
        "iat": agora,
        "exp": agora + 600,
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------
class TestTokenForjado:
    """Todo token que não foi emitido pelo core-api tem de dar 401.

    Sabotagem que este grupo pega: trocar `algorithms=[_ALGORITHM]` por
    `options={"verify_signature": False}` em `decode_access_token`, ou
    aceitar qualquer `typ`.
    """

    async def _status(self, token: str) -> int:
        async with _cliente() as c:
            r = await c.get(
                "/api/v1/calendar/calendars",
                headers={"Authorization": f"Bearer {token}"},
            )
            return r.status_code

    async def test_alg_none(self) -> None:
        import base64
        import json

        def b64(d: dict) -> str:
            return (
                base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
            )

        token = b64({"alg": "none", "typ": "JWT"}) + "." + b64(_claims()) + "."
        assert await self._status(token) == 401

    async def test_chave_errada(self) -> None:
        assert (
            await self._status(jwt.encode(_claims(), "x" * 40, algorithm="HS256"))
            == 401
        )

    async def test_assinado_com_o_segredo_de_servico(self) -> None:
        """O segredo core-api -> worker não pode abrir rota de usuário."""
        token = jwt.encode(
            _claims(), os.environ["SERVICE_JWT_SECRET"], algorithm="HS256"
        )
        assert await self._status(token) == 401

    async def test_expirado(self) -> None:
        token = jwt.encode(
            _claims(exp=int(time.time()) - 5),
            os.environ["JWT_SIGNING_KEY"],
            algorithm="HS256",
        )
        assert await self._status(token) == 401

    async def test_tipo_de_token_errado(self) -> None:
        token = jwt.encode(
            _claims(typ="service"), os.environ["JWT_SIGNING_KEY"], algorithm="HS256"
        )
        assert await self._status(token) == 401

    async def test_sem_exp(self) -> None:
        claims = _claims()
        del claims["exp"]
        token = jwt.encode(claims, os.environ["JWT_SIGNING_KEY"], algorithm="HS256")
        assert await self._status(token) == 401

    async def test_assinatura_valida_mas_conta_inexistente(self) -> None:
        """Token bem assinado para um usuário que não existe: 401.

        É o caso de conta apagada com token ainda dentro dos 15 minutos.
        """
        token = jwt.encode(_claims(), os.environ["JWT_SIGNING_KEY"], algorithm="HS256")
        assert await self._status(token) == 401


class TestSessaoEncerrada:
    async def test_logout_mata_o_access_token(self) -> None:
        """Sabotagem: tirar a checagem de `revoked_at` de `get_db`."""
        async with _cliente() as c:
            auth = await _conta(c)
            assert (
                await c.get("/api/v1/calendar/calendars", headers=auth)
            ).status_code == 200
            r = await c.post("/api/v1/auth/logout", headers=auth)
            assert r.status_code == 204
            depois = await c.get("/api/v1/calendar/calendars", headers=auth)
            assert depois.status_code == 401
            assert depois.json()["detail"]["error"]["code"] == "session_revoked"


# ---------------------------------------------------------------
# Isolamento entre contas
# ---------------------------------------------------------------
class TestIsolamentoEntreContas:
    """O RLS de §4.2, visto de fora.

    Sabotagem: `ALTER TABLE core.local_events NO FORCE ROW LEVEL
    SECURITY`, ou rodar o runtime como dono da tabela.
    """

    async def test_b_nao_ve_evento_de_a(self) -> None:
        async with _cliente() as c:
            a, b = await _conta(c), await _conta(c)
            await _evento(c, a, "Segredo de A")
            r = await c.get("/api/v1/calendar/events", params=JANELA, headers=b)
            assert r.status_code == 200
            assert all(e["title"] != "Segredo de A" for e in r.json()["events"])

    async def test_b_nao_altera_nem_apaga_evento_de_a(self) -> None:
        async with _cliente() as c:
            a, b = await _conta(c), await _conta(c)
            ev = await _evento(c, a, "Intocável")

            r = await c.patch(
                f"/api/v1/calendar/events/{ev['id']}",
                json={"title": "invadido"},
                headers=b,
            )
            assert r.status_code == 404

            r = await c.patch(
                f"/api/v1/calendar/events/{ev['id']}/flexibility",
                json={"flexibility": "flexible"},
                headers=b,
            )
            assert r.status_code == 404

            await c.delete(f"/api/v1/calendar/events/{ev['id']}", headers=b)

            meus = await c.get("/api/v1/calendar/events", params=JANELA, headers=a)
            titulos = [e["title"] for e in meus.json()["events"]]
            assert "Intocável" in titulos
            assert "invadido" not in titulos

    async def test_b_nao_cria_evento_no_calendario_de_a(self) -> None:
        """O furo achado na auditoria: antes isto devolvia 201.

        O RLS de `local_events` olha só `user_id`, e a chave estrangeira
        para `local_calendars` é verificada pelo Postgres sem RLS, então
        o evento de B nascia apontando para a agenda de A.

        Sabotagem: remover a checagem de dono em `LocalCalendar.create_event`.
        """
        async with _cliente() as c:
            a, b = await _conta(c), await _conta(c)
            ev = await _evento(c, a)
            r = await c.post(
                "/api/v1/calendar/events",
                json={
                    "title": "intruso",
                    "calendar_id": ev["calendar_id"],
                    "start": "2026-11-11T14:00:00-03:00",
                    "end": "2026-11-11T15:00:00-03:00",
                },
                headers=b,
            )
            assert r.status_code == 400, r.text

    async def test_b_nao_ve_tarefa_de_a(self) -> None:
        async with _cliente() as c:
            a, b = await _conta(c), await _conta(c)
            r = await c.post(
                "/api/v1/tasks", json={"title": "Tarefa secreta de A"}, headers=a
            )
            assert r.status_code == 201, r.text
            tarefa = r.json()

            lista_b = await c.get("/api/v1/tasks", headers=b)
            assert all(t["title"] != "Tarefa secreta de A" for t in lista_b.json())

            r = await c.patch(
                f"/api/v1/tasks/{tarefa['id']}", json={"title": "x"}, headers=b
            )
            assert r.status_code == 404

            r = await c.post(f"/api/v1/tasks/{tarefa['id']}/complete", headers=b)
            assert r.status_code == 404


# ---------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------
class TestLimiteDeLogin:
    async def test_login_por_ip_bloqueia_com_retry_after(self, monkeypatch) -> None:
        """Sabotagem: não chamar `enforce("login:ip", ...)`.

        O limite por IP é baixado para 3 aqui para o teste não precisar
        de 21 tentativas; o mecanismo é o mesmo.
        """
        from app.core.config import get_settings

        monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
        monkeypatch.setattr(get_settings(), "rate_limit_login_per_ip", "3/900")

        ip = _ip()
        async with _cliente() as c:
            codigos = []
            for i in range(5):
                r = await c.post(
                    "/api/v1/auth/login",
                    json={
                        "email": f"x{i}-{uuid.uuid4().hex[:6]}@age.dev.br",
                        "password": "errada",
                    },
                    headers={"X-Forwarded-For": ip},
                )
                codigos.append(r.status_code)
            assert codigos[:3] == [401, 401, 401]
            assert codigos[3] == 429
            assert int(r.headers["Retry-After"]) >= 1


# ---------------------------------------------------------------
# Upload
# ---------------------------------------------------------------
class TestUpload:
    async def test_arquivo_grande_e_recusado(self) -> None:
        """Sabotagem: aceitar acima de 5 MB."""
        async with _cliente() as c:
            auth = await _conta(c)
            grande = b"\x89PNG\r\n\x1a\n" + b"\x00" * (6 * 1024 * 1024)
            r = await c.post(
                "/api/v1/attachments",
                files={"file": ("x.png", grande, "image/png")},
                headers=auth,
            )
            assert r.status_code == 422
            assert "5 MB" in r.text

    async def test_extensao_mentirosa_e_recusada(self) -> None:
        """HTML com nome de imagem: o tipo vem do conteúdo, não do nome."""
        async with _cliente() as c:
            auth = await _conta(c)
            r = await c.post(
                "/api/v1/attachments",
                files={"file": ("foto.png", b"<script>alert(1)</script>", "image/png")},
                headers=auth,
            )
            assert r.status_code == 422


# ---------------------------------------------------------------
# Superfície exposta
# ---------------------------------------------------------------
def test_docs_somem_em_producao() -> None:
    """Sabotagem: voltar `docs_url="/docs"` fixo em `app/main.py`.

    Em subprocesso porque `_EXPOR_DOCS` é lido na importação do módulo.
    """
    codigo = (
        "from app.main import app;print(app.docs_url, app.openapi_url, app.redoc_url)"
    )
    env = {**os.environ, "ENVIRONMENT": "production"}
    saida = subprocess.run(
        [sys.executable, "-c", codigo],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert saida == "None None None"
