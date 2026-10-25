"""Registro e despacho de ferramentas.

ARQUITETURA.md §5.2. O despacho é por nome, vindo de um registro — não
há `if name == "get_events"` no orquestrador.

As ferramentas de ESCRITA não recebem acesso ao provedor de calendário.
Isso não é descuido: é o que torna estruturalmente impossível uma tool
de escrita tocar a agenda. Ela só pode chamar o `PlanBuilder`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.ports.llm import ToolSpec

log = logging.getLogger("ai-worker.tools")


class ToolCategory(StrEnum):
    READ = "read"
    WRITE = "write"
    RECORD = "record"


@dataclass
class ToolContext:
    """O que uma ferramenta pode acessar.

    `plan_builder` é um objeto opaco aqui para o núcleo do worker não
    importar o domínio do core-api: as tools de escrita o recebem já
    construído pelo chamador.
    """

    user_id: UUID
    conversation_id: UUID
    timezone: str
    # Leitura de agenda: callback injetado, não acesso direto ao banco.
    read_calendar: Callable[..., Awaitable[Any]] | None = None
    plan_builder: Any | None = None
    preferences: dict[str, Any] = field(default_factory=dict)
    # Busca na web: mesmo padrão de injeção do `read_calendar` — a tool
    # fica pura e o cliente HTTP mora fora do domínio (§5.10).
    search_web: Callable[..., Awaitable[Any]] | None = None
    # Orçamento de busca, decidido pelo BOTÃO da pessoa e não pelo
    # modelo: aprofundado lê o conteúdo das páginas (~6.500 tokens
    # contra ~1.200). Deixar o modelo escolher seria deixá-lo gastar.
    deep_search: bool = False


ToolHandler = Callable[[ToolContext, dict], Awaitable[dict]]


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    description: str
    parameters: dict
    category: ToolCategory
    handler: ToolHandler


_REGISTRY: dict[str, RegisteredTool] = {}


def register_tool(
    *,
    name: str,
    description: str,
    parameters: dict,
    category: ToolCategory,
) -> Callable[[ToolHandler], ToolHandler]:
    def decorator(handler: ToolHandler) -> ToolHandler:
        if name in _REGISTRY:
            raise ValueError(f"ferramenta '{name}' já registrada")
        _REGISTRY[name] = RegisteredTool(
            name=name,
            description=description,
            parameters=parameters,
            category=category,
            handler=handler,
        )
        return handler

    return decorator


# Ferramentas que dependem da API de busca do provedor (§5.1.3). Ficam
# numa constante, e não num `if nome.startswith("web_")`: o prefixo é
# convenção de nome, e uma tool nova chamada `web_hook` entraria na
# regra sem querer.
TOOLS_DE_WEB = frozenset({"web_search", "web_fetch"})


def build_tool_specs(
    *,
    include_write: bool = True,
    include_record: bool = True,
    include_web: bool = True,
) -> list[ToolSpec]:
    """Especificações para enviar ao modelo.

    `include_write=False` serve a um caso real: num turno em que o
    usuário só fez uma pergunta, oferecer tools de escrita convida o
    modelo a propor mudança que ninguém pediu (§5.9).
    """
    specs: list[ToolSpec] = []
    for tool in _REGISTRY.values():
        if tool.category == ToolCategory.WRITE and not include_write:
            continue
        if tool.category == ToolCategory.RECORD and not include_record:
            continue
        # Provedor sem busca na web não recebe as tools de busca: elas
        # falhariam em toda chamada, e o modelo tentaria de novo.
        if tool.name in TOOLS_DE_WEB and not include_web:
            continue
        specs.append(
            ToolSpec(
                name=tool.name,
                description=tool.description,
                parameters=tool.parameters,
            )
        )
    return sorted(specs, key=lambda s: s.name)


async def execute_tool(
    name: str, arguments: dict, context: ToolContext
) -> str:
    """Executa e devolve JSON para o modelo.

    O resultado é sempre string JSON porque é o que vai no `content` de
    uma mensagem `role="tool"`. Erro de ferramenta volta como dado, não
    como exceção: o modelo pode tentar outro caminho (§5.1).
    """
    tool = _REGISTRY.get(name)
    if tool is None:
        return json.dumps(
            {"error": f"ferramenta '{name}' não existe"}, ensure_ascii=False
        )

    try:
        result = await tool.handler(context, arguments)
    except Exception as exc:  # noqa: BLE001
        log.warning("ferramenta %s falhou: %s", name, exc)
        return json.dumps({"error": str(exc)}, ensure_ascii=False)

    return json.dumps(result, ensure_ascii=False, default=str)


def tool_category(name: str) -> ToolCategory | None:
    tool = _REGISTRY.get(name)
    return tool.category if tool else None


def registered_names() -> list[str]:
    return sorted(_REGISTRY)
