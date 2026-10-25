"""Busca na web pela API de ferramentas da DeepSeek.

ARQUITETURA.md §5.1.3. Espelha `calendar_client.py`: cliente HTTP no
worker, injetado no `ToolContext` como callback, para o domínio
continuar puro.

A autenticação é o **mesmo** bearer do chat — a chave já resolvida do
turno, que pode ser a do usuário ou a da instância. Não há chave nova
nem conta nova para o usuário administrar, e é isso que torna esta
feature barata: a credencial já chega decifrada em `run_turn`.

## O que eu medi antes de escrever isto

Chamei os dois endpoints com a chave real, em 24/10/2026:

| Chamada | Resultado |
|---|---|
| `search` "edital Enem 2026" | 200 em 0,9 s, 10 fontes reais (UFRGS, GOV.BR) |
| `fetch` de página real | 200 em 0,6–1,1 s, `{content, title}` em Markdown |
| `fetch` de URL inexistente | 404 `not_found_error` |
| primeiro `fetch` que tentei | **504 depois de 30,6 s** |

Três decisões saem direto dessa tabela.

**O parâmetro de quantidade chama `count`.** Testei `top_k`,
`max_results` e `count`: os dois primeiros são **ignorados em silêncio**
(voltaram 10 resultados), só o terceiro funciona. Mandar o nome errado
não dá erro — dá custo.

**`timeout` é obrigatório.** Aquele 504 de 30,6 s aconteceu na primeira
chamada que fiz na vida a este endpoint. Sem `timeout`, uma página lenta
prende o turno e o usuário olha um cursor piscando.

**Erro volta como dado.** `execute_tool` já documenta que o modelo pode
tentar outro caminho; uma exceção aqui mataria o turno inteiro por causa
de um site fora do ar.

## Por que o conteúdo das páginas não é "mais contexto de graça"

Medi o tamanho da carga, e ele decide o desenho da ferramenta:

| Chamada | Tokens |
|---|---|
| `count=3`, sem conteúdo | ~1.163 |
| `count=5`, `need_content=true` | ~6.455 |
| `count=10`, `need_content=true` | ~17.867 |

17.867 tokens é **mais que o prompt inteiro do Age** (~12.850). E o
histórico é relido a cada mensagem da conversa, então uma busca gorda
encarece todos os turnos seguintes, não só o seu. É o mesmo motivo do
limite de 50 páginas no PDF (§5.1.2).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger("ai-worker.web")

# A base da API de ferramentas. Fica junto do cliente, não em `config`,
# porque é fato sobre ESTE provedor — do mesmo jeito que `api_base` vive
# no adaptador da DeepSeek, não no ambiente.
BASE_PADRAO = "https://api.deepseek.com/v1"

# Medi um 504 de 30,6 s num `fetch`. 25 s dá folga para página lenta sem
# deixar o turno pendurado; o `search` responde em ~1 s e nunca chega
# perto disto.
TIMEOUT_BUSCA = 20.0
TIMEOUT_PAGINA = 25.0


class WebIndisponivel(RuntimeError):
    """Falha de rede ou recusa do provedor.

    Quem chama transforma isto em dado para o modelo — nunca deixa
    subir, porque site fora do ar não é motivo para matar o turno.
    """


class WebClient:
    """Cliente de busca e leitura de página, por turno.

    Recebe a chave já resolvida (do usuário ou da instância). Não
    guarda nada entre turnos.
    """

    def __init__(self, api_key: str, *, base_url: str | None = None) -> None:
        self._key = api_key
        self._base = (base_url or BASE_PADRAO).rstrip("/")

    async def __call__(self, operation: str, **kwargs: Any) -> Any:
        """Despacho por nome, para casar com o callback das tools."""
        match operation:
            case "search":
                return await self.search(**kwargs)
            case "fetch":
                return await self.fetch(**kwargs)
            case _:
                raise ValueError(f"operação desconhecida: {operation}")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }

    async def _post(self, path: str, corpo: dict, *, prazo: float) -> Any:
        url = f"{self._base}{path}"
        try:
            async with httpx.AsyncClient(timeout=prazo) as client:
                resposta = await client.post(url, json=corpo, headers=self._headers())
        except httpx.TimeoutException as exc:
            raise WebIndisponivel(f"tempo esgotado em {path}") from exc
        except httpx.HTTPError as exc:
            raise WebIndisponivel(f"falha de rede em {path}") from exc

        if resposta.status_code >= 400:
            # A mensagem do provedor é útil (`not_found_error` quando a
            # URL não existe), mas não vai crua para o modelo: pode
            # conter conteúdo de terceiro.
            log.warning("web %s recusado: %s", path, resposta.status_code)
            raise WebIndisponivel(f"o provedor recusou ({resposta.status_code})")
        return resposta.json()

    async def search(
        self, *, query: str, count: int = 3, need_content: bool = False
    ) -> list[dict]:
        """Busca na web.

        `count` é o nome certo do parâmetro — `top_k` e `max_results`
        são aceitos e ignorados (testei os três).
        """
        corpo: dict[str, Any] = {"query": query, "count": count}
        if need_content:
            corpo["need_content"] = True
        dados = await self._post("/tools/search", corpo, prazo=TIMEOUT_BUSCA)
        resultados = dados.get("results") or []
        if not isinstance(resultados, list):
            raise WebIndisponivel("resposta de busca em formato inesperado")
        return resultados

    async def fetch(self, *, url: str) -> dict:
        """Lê uma página como Markdown.

        Devolve `{content, title}`. Página que o provedor não consegue
        ler volta 404, e aí é `WebIndisponivel` — nunca conteúdo vazio
        fingindo sucesso.
        """
        dados = await self._post("/tools/fetch", {"url": url}, prazo=TIMEOUT_PAGINA)
        return {
            "title": dados.get("title") or "",
            "content": dados.get("content") or "",
        }
