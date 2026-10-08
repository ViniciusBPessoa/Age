"""Registro de provedores de LLM.

ARQUITETURA.md §3.1. Um decorador registra a classe; não existe
`if provider == "x"` em nenhum lugar do sistema.

Adicionar um provedor novo = criar um arquivo neste diretório com
`@register_llm`. Nada mais muda.
"""

from __future__ import annotations

from app.ports.llm import LLMProvider, NotSupported

_REGISTRY: dict[str, type[LLMProvider]] = {}


def register_llm(cls: type[LLMProvider]) -> type[LLMProvider]:
    if not getattr(cls, "key", ""):
        raise ValueError(f"{cls.__name__} precisa de `key`")
    if cls.key in _REGISTRY:
        raise ValueError(f"provedor '{cls.key}' já registrado")
    _REGISTRY[cls.key] = cls
    return cls


def build_llm(
    key: str,
    *,
    api_key: str,
    model: str | None = None,
    base_url: str | None = None,
) -> LLMProvider:
    cls = _REGISTRY.get(key)
    if cls is None:
        raise NotSupported(f"provedor desconhecido: {key}")
    return cls(  # type: ignore[call-arg]
        api_key=api_key,
        model=model or cls.default_model,
        base_url=base_url,
    )


def available_providers() -> list[dict]:
    """Catálogo para a tela de onboarding e o seletor (§8.1)."""
    out = []
    for cls in _REGISTRY.values():
        out.append(
            {
                "key": cls.key,
                "name": cls.display_name,
                "models": list(cls.known_models),
                "default_model": cls.default_model,
            }
        )
    return sorted(out, key=lambda p: p["name"])


def is_registered(key: str) -> bool:
    return key in _REGISTRY
