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


def model_sees_images(key: str, model: str | None) -> bool:
    """Se este provedor/modelo aceita imagem, SEM instanciar o provedor.

    Existe porque quem decide se vale buscar os anexos do banco nao tem
    chave de API em maos: a consulta acontece enquanto a sessao de banco
    esta aberta, e o provedor so e construido depois, com a credencial
    ja decifrada.

    Ler `supports_vision` da tabela de capacidades e consulta por
    atributo, nao `if provider_key == "deepseek"` -- e a mesma regra de
    §3.1.2. Provedor desconhecido devolve `False`: o lado seguro e nao
    buscar bytes que ninguem vai usar.
    """
    cls = _REGISTRY.get(key)
    if cls is None:
        return False
    caps = cls._capabilities_by_model.get(  # noqa: SLF001
        model or cls.default_model
    )
    if caps is None:
        caps = getattr(cls, "_fallback_capabilities", None)
    return bool(caps is not None and caps.supports_vision)


def is_registered(key: str) -> bool:
    return key in _REGISTRY
