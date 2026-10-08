"""Registro de provedores de tarefa.

ARQUITETURA.md §3.3. Mesmo padrão das outras duas portas: um decorador
registra, e não existe `if provider == "google"` fora de `adapters/`.
"""

from __future__ import annotations

from app.ports.tasks import TaskNotSupported, TaskProvider

_REGISTRY: dict[str, type[TaskProvider]] = {}


def register_tasks(cls: type[TaskProvider]) -> type[TaskProvider]:
    if not getattr(cls, "key", ""):
        raise ValueError(f"{cls.__name__} precisa de `key`")
    if cls.key in _REGISTRY:
        raise ValueError(f"provedor de tarefa '{cls.key}' já registrado")
    _REGISTRY[cls.key] = cls
    return cls


def get_tasks_class(key: str) -> type[TaskProvider]:
    cls = _REGISTRY.get(key)
    if cls is None:
        raise TaskNotSupported(f"provedor de tarefa desconhecido: {key}")
    return cls


def available_task_providers() -> list[dict]:
    out = [{"key": cls.key, "name": cls.display_name} for cls in _REGISTRY.values()]
    return sorted(out, key=lambda p: p["name"])


def is_registered(key: str) -> bool:
    return key in _REGISTRY
