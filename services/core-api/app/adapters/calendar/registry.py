"""Registro de provedores de calendário.

ARQUITETURA.md §3.2. Mesmo padrão da porta de LLM: um decorador
registra, e não existe `if provider == "google"` fora de `adapters/`.
"""

from __future__ import annotations

from app.ports.calendar import CalendarProvider, NotSupported

_REGISTRY: dict[str, type[CalendarProvider]] = {}


def register_calendar(cls: type[CalendarProvider]) -> type[CalendarProvider]:
    if not getattr(cls, "key", ""):
        raise ValueError(f"{cls.__name__} precisa de `key`")
    if cls.key in _REGISTRY:
        raise ValueError(f"provedor de calendário '{cls.key}' já registrado")
    _REGISTRY[cls.key] = cls
    return cls


def get_calendar_class(key: str) -> type[CalendarProvider]:
    cls = _REGISTRY.get(key)
    if cls is None:
        raise NotSupported(f"provedor de calendário desconhecido: {key}")
    return cls


def available_calendar_providers() -> list[dict]:
    out = []
    for cls in _REGISTRY.values():
        out.append({"key": cls.key, "name": cls.display_name})
    return sorted(out, key=lambda p: p["name"])


def is_registered(key: str) -> bool:
    return key in _REGISTRY
