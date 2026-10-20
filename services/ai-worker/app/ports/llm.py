"""Porta `LLMProvider` — o contrato único de todo provedor de modelo.

ARQUITETURA.md §3.1. Os tipos aqui são PRÓPRIOS do Age, não os de
nenhum provedor. Isso parece excesso de cerimônia até o segundo
adaptador entrar: DeepSeek é compatível com o formato da OpenAI, e
adotá-lo como "neutro" pareceria conveniente — até o Gemini chegar, que
não tem id de tool call, usa `role: "model"` e manda o system prompt num
campo separado (§3.1.1).

Nada neste arquivo importa SDK, httpx ou qualquer infraestrutura. É a
invariante verificada em §10.2.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal


# ---------------------------------------------------------------
# Mensagens e ferramentas — formato canônico
# ---------------------------------------------------------------
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON Schema


@dataclass(frozen=True)
class ToolCall:
    """Pedido de ferramenta, já normalizado.

    `arguments` chega SEMPRE como dict, mesmo em provedores que mandam
    string JSON (OpenAI, DeepSeek). A tradução é do adaptador.

    `id` existe sempre no canônico. O Gemini não tem ids, então o
    adaptador gera um sintético determinístico — sem isso, o histórico
    não consegue casar resposta com chamada quando o modelo pede duas
    ferramentas no mesmo turno (§3.1.1).
    """

    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class TextPart:
    """Um trecho de texto dentro de uma mensagem multimodal."""

    text: str


@dataclass(frozen=True)
class ImagePart:
    """Uma imagem anexada pelo usuário.

    Guardamos os BYTES, não uma URL. Duas razões: o provedor aceita
    `data:` embutido e com isso não precisamos expor o arquivo numa URL
    pública (§4.1), e um link que o provedor busca sozinho seria um
    caminho de saída de dados que ninguém auditou.

    `media_type` é detectado pelo CONTEÚDO, nunca pela extensão — quem
    anexa `.png` com bytes de JPEG não deve receber erro do provedor, e
    quem renomeia um executável para `.png` não deve chegar tão longe.
    """

    data: bytes
    media_type: Literal["image/jpeg", "image/png", "image/gif", "image/webp"]


ContentPart = TextPart | ImagePart


@dataclass(frozen=True)
class Message:
    """Uma mensagem no formato canônico.

    `content` é `str` no caso comum e lista de partes quando há imagem.
    A união existe para não reescrever as 31 ferramentas e todo o
    histórico por causa de um anexo: quem só manda texto continua
    passando `str`, e o adaptador é que decide o formato do wire.

    Não vale uma lista com UMA parte de texto como forma canônica de
    mensagem de texto — seriam dois jeitos de dizer a mesma coisa, e
    todo comparador de histórico passaria a ter dois casos.
    """

    role: Literal["system", "user", "assistant", "tool"]
    content: str | tuple[ContentPart, ...] | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True)
class Usage:
    """Tokens de UMA chamada ao provedor.

    `cached_prompt_tokens` é a parte de `prompt_tokens` que veio de
    cache — SUBCONJUNTO, não adicional. O DeepSeek devolve
    `prompt_cache_hit_tokens` e `prompt_cache_miss_tokens`, e a soma dos
    dois é `prompt_tokens`; tratar o hit como extra contaria o prompt
    duas vezes.

    `None` é "o provedor não informou", e é diferente de `0`
    ("informou que nada veio de cache"). A distinção não é teórica: com
    prompt grande e estável, `0` sustentado é sinal de que o cache não
    está pegando, e um default zero apagaria esse sinal.

    Os campos novos vêm DEPOIS dos dois originais e com default porque
    o teste de contrato constrói `Usage(1_000_000, 1_000_000)`
    posicionalmente.
    """

    prompt_tokens: int
    completion_tokens: int
    cached_prompt_tokens: int | None = None

    @property
    def uncached_prompt_tokens(self) -> int:
        """A parte do prompt cobrada como cache miss."""
        if self.cached_prompt_tokens is None:
            return self.prompt_tokens
        # `max(0, ...)` porque provedor pode reportar incoerente, e
        # custo negativo é pior que custo superestimado.
        return max(0, self.prompt_tokens - self.cached_prompt_tokens)


@dataclass(frozen=True)
class Completion:
    message: Message
    usage: Usage
    finish_reason: Literal["stop", "tool_calls", "length", "filter", "error"]


@dataclass(frozen=True)
class Chunk:
    """Fragmento de streaming.

    Texto e tool call são separados porque o orquestrador trata cada um
    de forma diferente: texto vai direto ao usuário, tool call é
    acumulado até o `finish_reason` (§5.1).
    """

    text_delta: str | None = None
    tool_call_delta: dict | None = None
    finish_reason: str | None = None
    usage: Usage | None = None
    done: bool = False


# ---------------------------------------------------------------
# Preço
# ---------------------------------------------------------------
@dataclass(frozen=True)
class Price:
    """Preço por 1M de tokens de um regime de cobrança.

    Três faixas porque é assim que se cobra quando há cache: o token de
    prompt servido de cache custa uma fração do mesmo token lido do
    zero. No DeepSeek a razão medida é 50x (0,003 contra 0,15 por
    milhão), e ignorá-la superestima o custo de um prompt grande e
    estável — que é exatamente a forma do prompt do Age.
    """

    input_cache_miss_per_1m: float
    output_per_1m: float
    # `None` = o provedor não cobra cache à parte, e o hit sai no preço
    # do miss. É o caso da OpenAI nesta tabela e do mock.
    input_cache_hit_per_1m: float | None = None

    def cost_usd(self, usage: Usage) -> float:
        hit = usage.cached_prompt_tokens or 0
        hit_rate = (
            self.input_cache_hit_per_1m
            if self.input_cache_hit_per_1m is not None
            else self.input_cache_miss_per_1m
        )
        return (
            usage.uncached_prompt_tokens / 1_000_000 * self.input_cache_miss_per_1m
            + hit / 1_000_000 * hit_rate
            + usage.completion_tokens / 1_000_000 * self.output_per_1m
        )


class Pricing(ABC):
    """Como um provedor precifica.

    O instante entra na pergunta porque há provedor cujo preço depende
    da hora. Quem consulta passa `when` e recebe um `Price`; nenhum
    chamador sabe se a resposta varia ou não.
    """

    @abstractmethod
    def price_at(self, when: datetime) -> Price: ...


@dataclass(frozen=True)
class FlatPricing(Pricing):
    """Preço único, sem variação por horário. O caso comum.

    Vive aqui, e não num adaptador, porque "não varia" não é
    característica de provedor nenhum em particular.
    """

    price: Price

    def price_at(self, when: datetime) -> Price:
        return self.price


# ---------------------------------------------------------------
# Capacidades
# ---------------------------------------------------------------
@dataclass(frozen=True)
class ModelCapabilities:
    """O que este provedor/modelo sabe fazer (§3.1.2).

    Consultado por atributo, nunca por `if provider_key == "gemini"`. O
    nome do provedor não aparece em nenhum ramo de decisão fora de
    `adapters/`.
    """

    supports_tools: bool
    supports_parallel_tool_calls: bool
    supports_streaming: bool
    supports_system_role: bool
    supports_json_mode: bool
    context_window: int
    max_output_tokens: int
    max_tools: int | None = None
    # Aceita `ImagePart` no conteúdo. Default `False` porque o padrão
    # seguro é recusar antes de gastar: mandar imagem para um modelo que
    # não vê devolve 400 do provedor, e o usuário já teria esperado o
    # upload inteiro para ouvir isso.
    supports_vision: bool = False
    # Teto por imagem que o provedor documenta. `None` = não declarado.
    # Medido no `deepseek-flash`: 184 tokens para 64x64 e 512x512
    # (idênticos — ele trabalha em blocos, então encolher abaixo de 512
    # não economiza), 652 para 1024x1024, 994 para 2048x2048.
    max_tokens_per_image: int | None = None
    # O TETO: preço da faixa mais cara, sem desconto de cache. Continua
    # aqui porque é o que a cota usa para errar para cima de propósito,
    # e o que o teste de contrato lê para saber se o modelo tem preço.
    cost_per_1m_input: float | None = None
    cost_per_1m_output: float | None = None
    # Tabela completa. `None` mantém o comportamento antigo (preço único
    # a partir dos dois campos acima), que é o que vale para mock,
    # modelo local e qualquer adaptador ainda não convertido.
    pricing: Pricing | None = None

    def estimate_cost_usd(
        self, usage: Usage, *, at: datetime | None = None
    ) -> float | None:
        """Custo estimado de UMA chamada.

        `at` é o instante da chamada, porque há provedor cujo preço
        depende da hora. Omitir usa o agora, o que é correto para quem
        calcula durante a própria chamada — e é o que mantém a
        assinatura de um argumento funcionando.

        `None` quando o modelo é local ou de preço desconhecido. Nunca
        `0.0`: zero afirmaria gratuidade.
        """
        if self.pricing is not None:
            return self.pricing.price_at(at or datetime.now(UTC)).cost_usd(usage)
        if self.cost_per_1m_input is None or self.cost_per_1m_output is None:
            return None
        return Price(
            input_cache_miss_per_1m=self.cost_per_1m_input,
            output_per_1m=self.cost_per_1m_output,
        ).cost_usd(usage)


# ---------------------------------------------------------------
# Erros tipados
# ---------------------------------------------------------------
class LLMError(Exception):
    """Base. `code` é o que o front mapeia para mensagem (§7.0)."""

    code = "llm_error"
    retryable = False

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider


class AuthenticationError(LLMError):
    code = "llm_auth_failed"


class RateLimitError(LLMError):
    code = "llm_rate_limited"
    retryable = True


class ProviderTimeout(LLMError):
    code = "llm_timeout"
    retryable = True


class ProviderUnavailable(LLMError):
    code = "llm_unavailable"
    retryable = True


class ContextTooLong(LLMError):
    code = "llm_context_too_long"


class MalformedToolCall(LLMError):
    """JSON de argumentos inválido ou truncado.

    Acontece de verdade com `finish_reason: "length"`. O orquestrador
    devolve o erro ao modelo para ele tentar de novo — uma vez só
    (§3.1.1).
    """

    code = "llm_malformed_tool_call"
    retryable = True


class NotSupported(LLMError):
    code = "llm_not_supported"


# ---------------------------------------------------------------
# A porta
# ---------------------------------------------------------------
class LLMProvider(ABC):
    """Contrato único. Um adaptador por provedor."""

    key: str
    display_name: str
    known_models: tuple[str, ...] = ()
    default_model: str = ""

    @property
    @abstractmethod
    def capabilities(self) -> ModelCapabilities:
        """Depende do MODELO escolhido, não só do provedor.

        gpt-4o tem tools; um modelo pequeno no Ollama não.
        """

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> Completion: ...

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> AsyncIterator[Chunk]: ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Valida a credencial sem gastar tokens relevantes."""

    @abstractmethod
    async def aclose(self) -> None:
        """Libera a conexão HTTP."""
