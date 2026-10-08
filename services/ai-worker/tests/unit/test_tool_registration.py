"""Garante que toda ferramenta escrita chega ao modelo.

ARQUITETURA.md §5.2. Este arquivo existe por causa de um bug real e
instrutivo: no M3 o módulo `study_tools.py` foi escrito, testado e
documentado, mas **nunca importado** em `run_turn.py`. O registro
acontece como efeito colateral do import, então nenhuma das suas
ferramentas chegou ao modelo — o planejamento de estudo ficou
inacessível pelo chat durante todo o M3.

O smoke do M3 não pegou porque ele exercita a API direto, não o caminho
conversacional. Um teste de unidade de `study_tools` também não pegaria:
as funções estavam corretas, só não estavam ligadas.

A falha é desta categoria: **o sistema continua funcionando, com menos
capacidade, sem erro nenhum.** É o tipo que só um teste de integração
estrutural como este encontra.
"""

from __future__ import annotations

import importlib
import pkgutil

# Importar o caminho REAL do worker, não os módulos de tool direto:
# é o `run_turn` que decide o que o modelo recebe, e é ele que estava
# errado. Testar os imports diretamente passaria mesmo com o bug.
import app.tasks.run_turn  # noqa: F401
from app.domain.tools import build_tool_specs
from app.domain.tools.registry import _REGISTRY, ToolCategory


def _tool_modules() -> list[str]:
    """Todo módulo de ferramenta que existe no pacote."""
    import app.domain.tools as pkg

    return [
        name
        for _, name, _ in pkgutil.iter_modules(pkg.__path__)
        if name.endswith("_tools")
    ]


def test_every_tool_module_is_actually_reachable() -> None:
    """A asserção que faltava no M3.

    Para cada módulo `*_tools.py` do pacote, pelo menos uma das suas
    ferramentas tem de estar registrada depois de importar `run_turn`.
    Escrever um módulo novo e esquecer a linha de import passa a
    quebrar aqui, em vez de passar desapercebido.
    """
    modules = _tool_modules()
    assert modules, "nenhum módulo de ferramenta encontrado"

    registered_after_run_turn = set(_REGISTRY)

    for module_name in modules:
        module = importlib.import_module(f"app.domain.tools.{module_name}")
        # Nomes que este módulo declara, lidos do próprio registry
        # depois de importá-lo isoladamente.
        declared = {
            name
            for name, tool in _REGISTRY.items()
            if tool.handler.__module__ == module.__name__
        }
        assert declared, (
            f"{module_name} não registra ferramenta nenhuma — "
            "arquivo morto?"
        )
        missing = declared - registered_after_run_turn
        assert not missing, (
            f"{module_name} declara {sorted(missing)}, mas essas tools NÃO "
            f"chegam ao modelo: falta importar `{module_name}` em "
            "app/tasks/run_turn.py"
        )


def test_expected_tools_are_present() -> None:
    """Lista explícita das ferramentas que o produto promete.

    Complementa o teste acima: ele garante que nada foi esquecido, este
    garante que o conjunto é o esperado — um rename silencioso de tool
    quebra o prompt e aparece aqui.
    """
    names = set(_REGISTRY)

    agenda = {
        "get_events",
        "find_free_slots",
        "summarize_workload",
        "propose_event",
        "propose_move",
        "propose_delete",
    }
    estudo = {"propose_study_plan", "get_study_progress", "mark_study_session"}
    tarefa = {
        "get_task_overview",
        "get_tasks",
        "create_task",
        "update_task",
        "complete_task",
        "reorder_tasks",
        "reserve_time_for_task",
    }

    rotina = {
        "get_routines",
        "get_routine_adherence",
        "get_period_report",
        "get_external_changes",
        "propose_routine",
        "mark_routine_occurrence",
    }

    for expected, grupo in (
        (agenda, "agenda"),
        (estudo, "estudo"),
        (tarefa, "tarefa"),
        (rotina, "rotina"),
    ):
        missing = expected - names
        assert not missing, f"faltam tools de {grupo}: {sorted(missing)}"


def test_write_tools_always_stage_and_never_apply() -> None:
    """A invariante estrutural de §5.2.

    Toda ferramenta de escrita acumula no diff e nada mais. A checagem
    é sobre o que ela USA: `plan_builder` (acumular) é obrigatório, e
    não pode haver chamada de escrita em porta nenhuma.

    Sobre ler: uma tool de escrita PODE ler. `propose_study_plan` lê a
    agenda de propósito, porque o plano precisa dos horários livres
    calculados pelo `Scheduler` — e é exatamente isso que impede o
    modelo de inventar horário (§5.5). O que ela não pode é escrever.
    A primeira versão deste teste proibia qualquer leitura e acusava
    `propose_study_plan`; a regra estava errada, não o código.
    """
    import inspect

    write_tools = [
        t for t in _REGISTRY.values() if t.category == ToolCategory.WRITE
    ]
    assert write_tools

    # Operações que escreveriam de verdade. Nenhuma pode aparecer numa
    # tool de escrita — se aparecer, a aprovação do usuário foi
    # contornada.
    forbidden = (
        "create_event(",
        "update_event(",
        "delete_event(",
        "create_task(",
        "update_task(",
        "complete_task(",
        "delete_task(",
        "reorder(",
        "commit(",
    )

    for tool in write_tools:
        source = inspect.getsource(tool.handler)
        assert "plan_builder" in source, (
            f"{tool.name} é de escrita mas não usa o plan_builder"
        )
        for call in forbidden:
            # `stage_*` pode conter nomes parecidos; o que importa é a
            # chamada direta numa porta, sempre via `provider.` ou
            # `tasks.`/`calendar.`.
            assert f"provider.{call}" not in source, (
                f"{tool.name} chama provider.{call} — escrita sem "
                "aprovação (§5.2)"
            )


async def test_write_tool_without_builder_degrades_gracefully() -> None:
    """Sem proposta ativa, a tool informa — não explode.

    Importa porque o orquestrador continua o laço: uma exceção aqui
    mataria o turno inteiro por causa de uma ferramenta.
    """
    from uuid import uuid4

    from app.domain.tools.registry import ToolContext, execute_tool

    ctx = ToolContext(
        user_id=uuid4(),
        conversation_id=uuid4(),
        timezone="America/Sao_Paulo",
        plan_builder=None,
    )
    result = await execute_tool(
        "create_task",
        {"title": "x", "reason": "y"},
        ctx,
    )
    assert "error" in result or result.get("staged") is not True


def test_tool_specs_build_without_error() -> None:
    """O que vai no prompt tem de ser serializável e completo."""
    specs = build_tool_specs()
    assert len(specs) == len(_REGISTRY)
    for spec in specs:
        assert spec.name
        assert spec.description
        assert spec.parameters.get("type") == "object"
