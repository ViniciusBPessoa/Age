"""Invariantes de arquitetura, verificadas em CI.

ARQUITETURA.md §3.4 e §10.2. Estas três regras existem porque, numa
arquitetura distribuída mantida por uma pessoa, a erosão acontece pelo
caminho mais curto: um import cruzado "só para reusar essa função", uma
query "só para evitar um request".

Documentar não basta. Precisa quebrar o build.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]
APP = SERVICE_ROOT / "app"


def _monorepo_root() -> Path | None:
    """Raiz do monorepo, quando visível.

    Dentro do container o serviço é montado em `/app`, sem o resto do
    repositório acima — então os testes que inspecionam outros serviços
    precisam ser saltados ali, não falhar. Rodam no CI, onde o checkout
    é completo.
    """
    for candidate in [SERVICE_ROOT, *SERVICE_ROOT.parents]:
        if (candidate / "services").is_dir() and (candidate / "packages").is_dir():
            return candidate
    return None

# SDKs e infraestrutura que o núcleo de domínio não pode conhecer.
FORBIDDEN_IN_DOMAIN = {
    "httpx",
    "requests",
    "aiohttp",
    "openai",
    "anthropic",
    "google",
    "googleapiclient",
    "sqlalchemy",
    "fastapi",
    "starlette",
    "redis",
    "arq",
    "asyncpg",
    "alembic",
}


def _imports_of(path: Path) -> set[str]:
    """Nomes de módulo de topo importados por um arquivo."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                found.add(node.module.split(".")[0])
    return found


def _py_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


# Bibliotecas de CÁLCULO PURO que o domínio pode importar. A regra de
# §3.4 proíbe infraestrutura, não dependência externa: o critério é
# **fazer I/O**, não ser de terceiros.
#
# `dateutil` entra aqui porque expande RRULE (RFC 5545) sem tocar rede,
# disco ou banco — mesma natureza de `zoneinfo`, que o domínio já usa.
# E escrever um expansor próprio seria pior que a dependência: a
# especificação tem casos de borda reais (BYSETPOS, UNTIL em UTC com
# DTSTART local, virada de horário de verão no meio da série) e errar
# neles põe a academia de alguém no dia errado.
ALLOWED_PURE_LIBRARIES = {
    "dateutil",
    "zoneinfo",
    "hypothesis",
}


def test_domain_has_no_infrastructure_imports() -> None:
    """O núcleo não conhece HTTP, SDK de provedor nem ORM.

    É o que torna as portas (§3) reais em vez de decorativas: se o
    domínio pudesse chamar o Google direto, a abstração não seria
    testável nem substituível.
    """
    offenders: list[str] = []
    for file in _py_files(APP / "domain"):
        bad = _imports_of(file) & FORBIDDEN_IN_DOMAIN
        if bad:
            offenders.append(f"{file.relative_to(SERVICE_ROOT)}: {sorted(bad)}")

    assert not offenders, "infraestrutura importada no domínio:\n" + "\n".join(
        offenders
    )


def test_domain_third_party_imports_are_declared_pure() -> None:
    """Nenhuma dependência externa entra no domínio sem ser avaliada.

    A lista negra de `FORBIDDEN_IN_DOMAIN` só pega o que alguém já
    pensou em proibir. Esta asserção é o complemento: todo import de
    terceiro no domínio tem de estar em `ALLOWED_PURE_LIBRARIES`, o que
    força a decisão a ser explícita em vez de passar por omissão.

    Foi o que aconteceu ao adicionar `dateutil` no M5 — a lista negra
    não reclamou, e sem este teste a pergunta "isso faz I/O?" nunca
    teria sido feita.
    """
    stdlib = set(sys.stdlib_module_names)
    internal = {"app", "age_contracts", "tests"}

    offenders: list[str] = []
    for file in _py_files(APP / "domain"):
        for module in _imports_of(file):
            if (
                module in stdlib
                or module in internal
                or module in ALLOWED_PURE_LIBRARIES
                or module.startswith("_")
            ):
                continue
            offenders.append(f"{file.relative_to(SERVICE_ROOT)}: {module}")

    assert not offenders, (
        "dependencia externa no dominio que nao foi declarada pura:\n"
        + "\n".join(offenders)
        + "\n\nSe ela nao faz I/O, acrescente a ALLOWED_PURE_LIBRARIES "
        "com o motivo. Se faz, ela pertence a um adaptador."
    )


def test_core_api_does_not_import_ai_worker() -> None:
    """Os dois serviços de backend não compartilham código.

    O único compartilhado é `age-contracts` (§2.5). Um import cruzado
    acoplaria os deploys e tornaria a separação de §2.3 ficção.
    """
    offenders: list[str] = []
    for file in _py_files(APP):
        text = file.read_text(encoding="utf-8")
        for marker in ("ai_worker", "ai-worker/app", "from app.tasks"):
            if marker in text and "age_contracts" not in text:
                # `AI_WORKER_INTERNAL_URL` é config, não import.
                if "INTERNAL_URL" in text or "ai_worker_internal_url" in text:
                    continue
                offenders.append(f"{file.relative_to(SERVICE_ROOT)}: {marker}")

    assert not offenders, "referência ao ai-worker:\n" + "\n".join(offenders)


def test_repositories_do_not_create_sessions() -> None:
    """Nenhum repositório abre sessão por conta própria.

    A sessão vem sempre de `user_session` ou `system_session` (§4.2.2),
    que setam `app.user_id`. Uma sessão crua ignoraria o RLS — e o bug
    só apareceria como vazamento entre contas.
    """
    offenders: list[str] = []
    for file in _py_files(APP / "repositories"):
        text = file.read_text(encoding="utf-8")
        for marker in ("_session_factory", "async_sessionmaker", "create_async_engine"):
            if marker in text:
                offenders.append(f"{file.relative_to(SERVICE_ROOT)}: {marker}")

    assert not offenders, "repositório criando sessão:\n" + "\n".join(offenders)


@pytest.mark.parametrize(
    "forbidden",
    ["pg", "postgres", "prisma", "drizzle", "openai", "@anthropic-ai"],
)
def test_web_has_no_business_dependencies(forbidden: str) -> None:
    """O BFF só repassa — a regra de §2.4.1, verificável.

    Sem este teste, a regra é só boa intenção escrita num documento: as
    API routes do Next são o caminho mais curto para lógica vazar para o
    front, onde não há os testes de §10.
    """
    root = _monorepo_root()
    if root is None:
        pytest.skip("fora do monorepo (container) — este teste roda no CI")

    pkg = root / "services" / "web" / "package.json"
    if not pkg.exists():
        pytest.skip("services/web não encontrado")

    import json

    data = json.loads(pkg.read_text(encoding="utf-8"))
    deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}

    assert forbidden not in deps, (
        f"'{forbidden}' no package.json do web: lógica de negócio ou acesso "
        f"a banco não vivem no front (§2.4.1)"
    )
