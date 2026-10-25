"""Job `run_turn`: executa um turno de conversa.

ARQUITETURA.md §5.1 e §2.3.1. Este arquivo é a cola entre o núcleo
(orquestrador, puro) e a infraestrutura (banco, Redis, provedor).

Responsabilidades, em ordem:
  1. guarda de idempotência — a fila é at-least-once (§5.1)
  2. resolve a credencial (do usuário ou da instância) e checa a cota
  3. monta o histórico canônico do banco
  4. roda o laço, publicando no stream
  5. persiste mensagens, uso e estado do turno
"""

from __future__ import annotations

import json
import logging
from uuid import UUID

from age_contracts import (
    DoneEvent,
    ErrorEvent,
    Fonte,
    PlanEvent,
    TokenEvent,
    ToolEndEvent,
    ToolStartEvent,
    TurnStatus,
    UsageEvent,
)
from sqlalchemy import text

from app import tracing
from app.adapters.llm.registry import build_llm, model_sees_images
from app.calendar_client import CalendarClient
from app.config import get_settings
from app.credential_resolver import NoCredential, QuotaExceeded
from app.credential_resolver import resolve as resolve_credential
from app.db import user_session
from app.domain.orchestrator import TurnCancelled, run_conversation_turn
from app.domain.tools import (
    ToolContext,
    build_tool_specs,
    # Importar estes módulos REGISTRA as ferramentas no registry (§5.2),
    # e os `noqa: F401` são obrigatórios: sem eles o lint remove o
    # import e as tools somem do prompt — silenciosamente, porque o
    # orquestrador continua funcionando com menos ferramentas.
    #
    # Foi exatamente isso que aconteceu com `study_tools` no M3: o
    # módulo foi escrito, testado e documentado, mas nunca importado
    # aqui. Nenhuma das suas ferramentas chegou ao modelo, e o smoke do
    # M3 não pegou porque ele exercita a API direto, não o caminho
    # conversacional.
    #
    # Toda tool nova precisa de uma linha aqui, e
    # `tests/unit/test_tool_registration.py` quebra se faltar.
    calendar_tools,  # noqa: F401
    execute_tool,
    routine_tools,  # noqa: F401
    study_tools,  # noqa: F401
    task_tools,  # noqa: F401
    web_tools,  # noqa: F401
)
from app.ports.llm import LLMError, Message
from app.prompts import build_system_prompt
from app.publishers import TurnPublisher
from app.staging import StagingBuilder
from app.web_client import WebClient

log = logging.getLogger("ai-worker.run_turn")


async def run_turn(ctx: dict, payload: dict) -> dict:
    turn_id = payload["turn_id"]
    user_id = UUID(payload["user_id"])
    conversation_id = UUID(payload["conversation_id"])
    # Botao do composer (secao 5.1.3). `.get` e nao `[...]`: job
    # enfileirado antes deste campo existir nao o traz.
    deep_search = bool(payload.get("deep_search", False))

    # Primeira linha do job, de propósito: daqui para baixo toda linha de
    # log — deste arquivo, do orquestrador, de cada tool, e também de
    # `httpx` e `sqlalchemy` — sai com o trace que começou no clique do
    # usuário (§12). É o ponto onde o trace deixava de existir: o
    # payload trazia `trace_id` desde o M0 e ninguém o lia.
    trace = tracing.bind(payload, user_id=str(user_id))

    pub = TurnPublisher(ctx["redis"], turn_id)
    settings = get_settings()

    # Um registro por turno com o id correlacionável, antes de qualquer
    # trabalho: se o turno pendurar e nunca mais logar, esta linha é a
    # única prova de que ele começou — e §12 lista "turnos em `running`
    # há mais de 5 min" como métrica justamente desse caso.
    log.info("turno %s iniciado (trace=%s)", turn_id, trace)

    try:
        # --- 1. Idempotência: job reentregue não refaz o turno ---
        # A fila garante at-least-once, não exactly-once: um job pode ser
        # reentregue se o worker morrer após pegar e antes de confirmar.
        # Sem esta guarda, o turno é refeito e os tokens cobrados de novo
        # (§5.1).
        #
        # O INSERT ... ON CONFLICT resolve criação e guarda numa ida só.
        # É o worker que cria a linha porque ele é o dono do schema `ai`;
        # o core-api só tem SELECT ali (§2.3.2).
        async with user_session(user_id) as db:
            row = await db.execute(
                text(
                    """
                    INSERT INTO ai.turns
                      (id, user_id, conversation_id, status, started_at)
                    VALUES (:tid, :uid, :cid, 'running', now())
                    ON CONFLICT (id) DO UPDATE
                      SET status = 'running', started_at = now()
                      WHERE ai.turns.status = 'queued'
                    RETURNING status
                    """
                ),
                {
                    "tid": turn_id,
                    "uid": str(user_id),
                    "cid": str(conversation_id),
                },
            )
            claimed = row.scalar_one_or_none()

            if claimed is None:
                # O WHERE do DO UPDATE não casou: outro worker já pegou,
                # ou o turno terminou. Descarta sem refazer.
                previous = (
                    await db.execute(
                        text("SELECT status FROM ai.turns WHERE id = :tid"),
                        {"tid": turn_id},
                    )
                ).scalar_one_or_none()
                log.info("turno %s já em %s, descartando duplicata", turn_id, previous)
                return {"status": "duplicate", "previous": previous}

        # --- 2. Credencial e provedor ---
        async with user_session(user_id) as db:
            # Chave do usuário se ele colou uma; senão a da instância,
            # com cota (§3451). A decisão inteira vive em
            # `credential_resolver`, inclusive a checagem de cota, para
            # que este arquivo continue sendo cola e não política.
            try:
                credencial = await resolve_credential(db, user_id, conversation_id)
            except NoCredential:
                await _fail(
                    pub,
                    db,
                    turn_id,
                    "no_credential",
                    "Você ainda não configurou uma chave de API. "
                    "Vá em Configurações → Modelos.",
                )
                return {"status": "failed", "reason": "no_credential"}
            except QuotaExceeded as cota:
                # Mensagem com os números: §5.9.13 pede degradação
                # acionável, e "cota excedida" sem dizer quanto nem o
                # que fazer não é acionável. A saída existe e é dita.
                await _fail(
                    pub,
                    db,
                    turn_id,
                    "quota_exceeded",
                    f"Você usou os US$ {cota.limit_usd:.2f} de uso "
                    "incluído deste mês. Para continuar agora, cole sua "
                    "própria chave de API em Configurações → Modelos — "
                    "aí não há limite. Senão, a cota volta no dia 1º.",
                )
                return {"status": "failed", "reason": "quota_exceeded"}

            provider_key = credencial.provider_key
            default_model = credencial.model
            base_url = credencial.base_url

            conv = (
                await db.execute(
                    text("SELECT model FROM core.conversations WHERE id = :cid"),
                    {"cid": str(conversation_id)},
                )
            ).first()
            model = (conv[0] if conv else None) or default_model

            # --- 3. Histórico canônico ---
            rows = await db.execute(
                text(
                    """
                    SELECT id, role, content, tool_calls, tool_call_id
                    FROM core.messages
                    WHERE conversation_id = :cid AND deleted_at IS NULL
                    ORDER BY created_at ASC
                    """
                ),
                {"cid": str(conversation_id)},
            )
            linhas_do_historico = rows.all()

            # Os anexos de TODAS as mensagens da conversa, numa consulta
            # so: uma por mensagem seria N+1 num laco que cresce com o
            # tamanho da conversa.
            #
            # So busca se o modelo ve imagem. Para modelo sem visao a
            # consulta seria trabalho jogado fora -- e pior, os bytes
            # viriam do banco para serem descartados.
            #
            # PDF nao depende de visao: ele chega como TEXTO extraido
            # (migration 0014), entao funciona em qualquer modelo. Por
            # isso os anexos sao sempre carregados, e e o filtro abaixo
            # que descarta a imagem quando o modelo nao a ve.
            ve_imagem = model_sees_images(provider_key, model)
            anexos_por_mensagem = await _carregar_anexos(
                db,
                conversation_id=conversation_id,
                incluir_imagens=ve_imagem,
            )

            if not ve_imagem:
                # Descarte CALADO e o que faz o modelo afirmar o que nao
                # fez: a pessoa manda a foto, a resposta vem sem ela, e
                # nada no caminho reclama. Se ha IMAGEM e o modelo nao
                # ve, isso vira linha de log -- e a tela so oferece o
                # botao quando o modelo aceita imagem, entao chegar aqui
                # com imagem significa que alguma ponta esta errada.
                tem_imagem = (
                    await db.execute(
                        text(
                            """
                            SELECT 1 FROM core.message_attachments
                            WHERE conversation_id = :cid
                              AND message_id IS NOT NULL
                              AND media_type <> 'application/pdf'
                            LIMIT 1
                            """
                        ),
                        {"cid": str(conversation_id)},
                    )
                ).first()
                if tem_imagem is not None:
                    log.warning(
                        "turno %s: imagem ignorada, modelo %s/%s nao ve imagem",
                        turn_id,
                        provider_key,
                        model,
                    )

            history = [
                _to_domain_message(r, anexos_por_mensagem) for r in linhas_do_historico
            ]

            summary_row = (
                await db.execute(
                    text(
                        """
                        SELECT summary FROM core.conversation_summaries
                        WHERE conversation_id = :cid
                        ORDER BY created_at DESC LIMIT 1
                        """
                    ),
                    {"cid": str(conversation_id)},
                )
            ).first()
            summary = summary_row[0] if summary_row else None

            prefs = (
                await db.execute(
                    text(
                        """
                        SELECT timezone FROM core.users WHERE id = :uid
                        """
                    ),
                    {"uid": str(user_id)},
                )
            ).first()
            tz = prefs[0] if prefs else "America/Sao_Paulo"

            # Preferencias para `get_user_preferences` (secao 5.2).
            pref_row = (
                await db.execute(
                    text(
                        """
                        SELECT session_min, session_max, break_min,
                               max_hours_per_day, buffer_min, working_hours,
                               blocked_windows, preferred_study_times
                        FROM core.user_preferences WHERE user_id = :uid
                        """
                    ),
                    {"uid": str(user_id)},
                )
            ).first()
            prefs_dict = (
                {
                    "session_min": pref_row[0],
                    "session_max": pref_row[1],
                    "break_min": pref_row[2],
                    "max_hours_per_day": pref_row[3],
                    "buffer_min": pref_row[4],
                    "working_hours": pref_row[5],
                    "blocked_windows": pref_row[6],
                    "preferred_study_times": pref_row[7],
                }
                if pref_row
                else {}
            )

        # A chave já vem decifrada do resolver — seja a do usuário (que
        # ele decifrou de `llm_credentials`) ou a da instância (que vem
        # do ambiente, sem cifra, porque não é dado de usuário).
        provider = build_llm(
            provider_key,
            api_key=credencial.api_key,
            model=model,
            base_url=base_url,
        )

        messages = [
            Message(role="system", content=build_system_prompt(timezone=tz)),
            *history,
        ]

        # --- 4. Ferramentas e o laço ---
        # O staging acumula propostas sem tocar a agenda (§5.2). As
        # tools de escrita só conhecem este objeto; é estruturalmente
        # impossível uma delas aplicar algo.
        # Consulta por atributo, nunca `if provider_key == "deepseek"`
        # (§3.1.2). `getattr` porque um adaptador pode não herdar da base
        # — o mock não herda, e acessar direto derrubava o turno inteiro
        # quando a visão entrou.
        tem_busca = bool(getattr(provider, "supports_web_search", False))

        staging = StagingBuilder(user_id=user_id, conversation_id=conversation_id)
        tool_ctx = ToolContext(
            user_id=user_id,
            conversation_id=conversation_id,
            timezone=tz,
            read_calendar=CalendarClient(user_id),
            plan_builder=staging,
            preferences=prefs_dict,
            # A busca usa a MESMA chave do chat, já decifrada pelo
            # resolver. Nenhuma credencial nova, nenhuma conta a mais
            # para o usuário administrar (§5.1.3).
            search_web=(
                WebClient(credencial.api_key) if tem_busca else None
            ),
            deep_search=deep_search,
        )

        # As fontes de TODAS as buscas do turno, para gravar na
        # mensagem. Sem isto elas vivem só no estado do navegador e um
        # F5 apaga a lista — a pessoa volta a não saber de onde veio.
        fontes_do_turno: list[dict] = []

        caps = provider.capabilities
        tools = (
            build_tool_specs(include_web=tem_busca)
            if caps.supports_tools
            else None
        )

        async def _run_tool(name: str, arguments: dict) -> str:
            return await execute_tool(name, arguments, tool_ctx)

        try:
            result = await run_conversation_turn(
                provider=provider,
                messages=messages,
                tools=tools,
                execute_tool=_run_tool,
                emit_text=lambda d: pub.publish(TokenEvent(delta=d)),
                emit_tool_start=lambda n: pub.publish(ToolStartEvent(name=n)),
                emit_tool_end=lambda n, ok, s, f=None: _emitir_fim(
                    pub, fontes_do_turno, n, ok, s, f
                ),
                is_cancelled=pub.is_cancelled,
                max_iterations=settings.max_tool_iterations,
                summary=summary,
            )
        finally:
            await provider.aclose()

        # --- 5. Persistência ---
        # O custo vem do orquestrador, somado POR CHAMADA. Recalcular
        # aqui sobre `result.usage` (o total do turno) aplicaria um
        # preço só a chamadas que podem ter acontecido em faixas de
        # preço diferentes — ver o comentário no laço do orquestrador.
        cost = result.cost_usd

        async with user_session(user_id) as db:
            # Rascunho de plano, se o modelo propos algo. A promocao
            # para `core.plans` e a validacao de conflito sao do
            # core-api, que e o dono da agenda (secao 2.3.2).
            if not staging.is_empty:
                await db.execute(
                    text(
                        """
                        INSERT INTO ai.plan_drafts
                          (turn_id, user_id, conversation_id, summary, items)
                        VALUES (:tid, :uid, :cid, :summary,
                                CAST(:items AS json))
                        """
                    ),
                    {
                        "tid": turn_id,
                        "uid": str(user_id),
                        "cid": str(conversation_id),
                        "summary": staging.summary(),
                        "items": staging.to_json(),
                    },
                )

            message_id = await _persist_assistant_message(
                db,
                turn_id=turn_id,
                user_id=user_id,
                conversation_id=conversation_id,
                text_content=result.text,
                provider_key=provider_key,
                model=model,
                usage=result.usage,
                cost_usd=cost,
                used_instance_key=credencial.is_instance,
                fontes=fontes_do_turno,
            )
            await db.execute(
                text(
                    """
                    UPDATE ai.turns
                    SET status='done', finished_at=now(),
                        provider_key=:pk, model=:model,
                        prompt_tokens=:pt, completion_tokens=:ct
                    WHERE id = :tid
                    """
                ),
                {
                    "tid": turn_id,
                    "pk": provider_key,
                    "model": model,
                    "pt": result.usage.prompt_tokens,
                    "ct": result.usage.completion_tokens,
                },
            )

        await pub.publish(
            UsageEvent(
                prompt_tokens=result.usage.prompt_tokens,
                completion_tokens=result.usage.completion_tokens,
                model=model or "?",
                provider_key=provider_key,
                cost_usd=cost,
                cached_prompt_tokens=result.usage.cached_prompt_tokens,
            )
        )
        if not staging.is_empty:
            # O front abre a UI de revisao ao receber isto (secao 8.1).
            await pub.publish(
                PlanEvent(
                    plan_id=turn_id,  # resolvido na promocao
                    item_count=len(staging.items),
                )
            )

        await pub.publish(DoneEvent(message_id=str(message_id), status=TurnStatus.DONE))
        return {
            "status": "done",
            "iterations": result.iterations,
            "cost_usd": cost,
        }

    except TurnCancelled:
        log.info("turno %s cancelado pelo usuário", turn_id)
        async with user_session(user_id) as db:
            await db.execute(
                text(
                    "UPDATE ai.turns SET status='cancelled', finished_at=now() "
                    "WHERE id = :tid"
                ),
                {"tid": turn_id},
            )
        await pub.publish(DoneEvent(message_id="", status=TurnStatus.CANCELLED))
        return {"status": "cancelled"}

    except LLMError as exc:
        log.warning("turno %s falhou no provedor: %s", turn_id, exc)
        async with user_session(user_id) as db:
            await _fail(pub, db, turn_id, exc.code, _friendly(exc))
        return {"status": "failed", "reason": exc.code}

    except Exception:  # noqa: BLE001
        log.exception("turno %s falhou inesperadamente", turn_id)
        async with user_session(user_id) as db:
            await _fail(
                pub,
                db,
                turn_id,
                "internal_error",
                "Algo falhou do nosso lado. Tente novamente.",
            )
        return {"status": "failed", "reason": "internal_error"}

    finally:
        # TTL sempre, inclusive em falha: stream órfão sem expiração
        # vaza memória no Redis.
        await pub.finish()


# ---------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------
async def _carregar_anexos(
    db, *, conversation_id, incluir_imagens: bool = True
) -> dict[str, list]:
    """Os anexos da conversa, agrupados por mensagem e na ordem.

    `ORDER BY position` nao e detalhe: "compare a primeira com a
    segunda" muda de significado se a ordem embaralhar, e a ordem que
    vale e a que a pessoa escolheu no composer.

    **Imagem e PDF saem daqui de formas diferentes.** A imagem vira
    `ImagePart` com os bytes; o PDF vira `TextPart` com o texto que o
    core-api ja extraiu no upload (migration 0014) -- o modelo nao
    aceita PDF, e reextrair aqui significaria `pypdf` no worker e o
    mesmo trabalho repetido a cada turno da conversa.

    Le `data` direto. O worker tem SELECT em `core.*` (secao 2.3.2), e
    os bytes vem do banco porque e la que eles estao -- ver o cabecalho
    da migration 0013 para por que nao num arquivo.
    """
    from app.ports.llm import ImagePart, TextPart

    rows = await db.execute(
        text(
            """
            SELECT message_id, media_type, data, filename, extracted_text
            FROM core.message_attachments
            WHERE conversation_id = :cid
              AND message_id IS NOT NULL
              AND (data IS NOT NULL OR extracted_text IS NOT NULL)
            ORDER BY message_id, position ASC
            """
        ),
        {"cid": str(conversation_id)},
    )
    por_mensagem: dict[str, list] = {}
    for message_id, media_type, dados, filename, texto in rows.all():
        if media_type == "application/pdf":
            if not texto:
                # A constraint `ck_attachment_pdf_tem_texto` impede
                # isto, mas um PDF sem texto aqui significaria mandar
                # ao modelo um anexo vazio -- pular e o lado seguro.
                continue
            por_mensagem.setdefault(str(message_id), []).append(
                TextPart(text=_pdf_como_texto(filename, texto))
            )
            continue
        # Modelo sem visao: a imagem e descartada AQUI, e o PDF da
        # mesma conversa continua passando. O aviso de descarte fica em
        # quem chama, que sabe o turno.
        if not incluir_imagens or dados is None:
            continue
        por_mensagem.setdefault(str(message_id), []).append(
            ImagePart(data=bytes(dados), media_type=media_type)
        )
    return por_mensagem


def _pdf_como_texto(filename: str | None, texto: str) -> str:
    """O texto do PDF, delimitado e com a origem declarada.

    Os delimitadores nao sao enfeite. Sem eles o conteudo do documento
    se mistura com a pergunta, e o modelo passa a tratar o que esta
    ESCRITO no PDF como instrucao -- um documento que contenha "ignore
    as instrucoes anteriores" vira injecao. Com a marcacao, ha uma
    fronteira declarada entre o que o usuario pede e o que o arquivo
    diz.

    O nome do arquivo entra porque "segundo o edital" so faz sentido se
    o modelo souber qual anexo e qual quando ha mais de um.
    """
    nome = filename or "documento.pdf"
    return (
        f"[Conteudo extraido do PDF anexado: {nome}]\n"
        "--- inicio do documento ---\n"
        f"{texto}\n"
        "--- fim do documento ---"
    )


def _to_domain_message(
    row, anexos_por_mensagem: dict[str, list] | None = None
) -> Message:
    from app.ports.llm import TextPart, ToolCall

    message_id, role, content, tool_calls_raw, tool_call_id = row
    calls: tuple[ToolCall, ...] = ()
    if tool_calls_raw:
        calls = tuple(
            ToolCall(id=c["id"], name=c["name"], arguments=c.get("arguments", {}))
            for c in tool_calls_raw
        )

    # Com anexo, `content` deixa de ser `str` e vira tupla de partes.
    # O texto vem PRIMEIRO: e a pergunta que diz o que olhar na imagem,
    # e o provedor le a sequencia na ordem em que ela chega.
    anexos = (anexos_por_mensagem or {}).get(str(message_id), [])
    conteudo: object = content
    if anexos:
        partes = []
        if content:
            partes.append(TextPart(text=content))
        partes.extend(anexos)
        conteudo = tuple(partes)

    return Message(
        role=role, content=conteudo, tool_calls=calls, tool_call_id=tool_call_id
    )


def _emitir_fim(pub, acumulador: list[dict], nome, ok, resumo, fontes):
    """Publica o `tool_end` e guarda as fontes para a persistência.

    As duas coisas juntas porque são o mesmo dado com dois destinos: a
    tela agora (evento) e a tela depois do F5 (mensagem gravada).
    """
    if fontes:
        for f in fontes:
            if f not in acumulador:
                acumulador.append(f)
    return pub.publish(
        ToolEndEvent(
            name=nome,
            ok=ok,
            summary=resumo,
            fontes=tuple(Fonte(**x) for x in fontes) if fontes else None,
        )
    )


async def _persist_assistant_message(
    db,
    *,
    turn_id: str,
    user_id: UUID,
    conversation_id: UUID,
    text_content: str,
    provider_key: str,
    model: str | None,
    usage,
    cost_usd: float | None,
    used_instance_key: bool,
    fontes: list[dict] | None = None,
) -> UUID:
    """Grava a saída do turno no schema `ai`.

    **Não** escreve em `core.messages` nem em `core.conversations`: o
    worker só tem SELECT ali (§2.3.2). Quem copia para `core` é o
    core-api, que é o dono daquele schema.

    Essa separação pareceu burocracia até o Postgres recusar a escrita
    na primeira execução real: `permission denied for table
    conversations`. O GRANT estava certo; o código estava errado.
    """
    row = await db.execute(
        text(
            """
            INSERT INTO ai.turn_outputs
              (turn_id, user_id, conversation_id, role, content,
               sequence, provider_key, model, tool_calls)
            VALUES (:tid, :uid, :cid, 'assistant', :content, 0, :pk,
                    :model, CAST(:fontes AS json))
            RETURNING id
            """
        ),
        {
            "tid": turn_id,
            "uid": str(user_id),
            "cid": str(conversation_id),
            "content": text_content,
            "pk": provider_key,
            "model": model,
            # As fontes da busca viajam na coluna `tool_calls`, que já
            # é copiada para `core.messages` pelo dreno. Reusar a coluna
            # evita migration para um dado que aparece em uma mensagem
            # a cada muitas — e o formato é discriminado por chave, então
            # quem lê sabe o que é.
            "fontes": (
                json.dumps({"fontes": fontes}, ensure_ascii=False)
                if fontes
                else None
            ),
        },
    )
    output_id = row.scalar_one()

    await db.execute(
        text(
            """
            INSERT INTO ai.turn_usage
              (turn_id, user_id, provider_key, model,
               prompt_tokens, completion_tokens, cached_prompt_tokens,
               estimated_cost_usd, used_instance_key)
            VALUES (:tid, :uid, :pk, :model, :pt, :ct, :cached,
                    :cost, :inst)
            """
        ),
        {
            "tid": turn_id,
            "uid": str(user_id),
            "pk": provider_key,
            "model": model or "?",
            "pt": usage.prompt_tokens,
            "ct": usage.completion_tokens,
            # A evidência do desconto de cache, para o custo poder ser
            # auditado contra os tokens que o geraram.
            "cached": usage.cached_prompt_tokens,
            "cost": cost_usd,
            # De quem foi o custo. É o que a consulta da cota filtra:
            # sem isto, turnos pagos pela chave do próprio usuário
            # descontariam da cota do operador.
            "inst": used_instance_key,
        },
    )
    return output_id


async def _fail(pub, db, turn_id: str, code: str, message: str) -> None:
    await db.execute(
        text(
            "UPDATE ai.turns SET status='failed', finished_at=now(), "
            "error_code=:code WHERE id = :tid"
        ),
        {"tid": turn_id, "code": code},
    )
    # `retryable` diz ao front se oferecer "tentar de novo", e para
    # estes dois a resposta é não: repetir não cria uma credencial nem
    # devolve cota, e um botão que não resolve nada é pior que nenhum.
    #
    # Era `code != "no_credential"` — uma lista de exceções com um item
    # só, então `quota_exceeded` entrou como repetível por omissão
    # quando passou a existir. Agora é um conjunto nomeado, que é o que
    # faz o próximo código deste tipo ser uma decisão e não um descuido.
    NAO_RESOLVE_REPETINDO = {"no_credential", "quota_exceeded"}
    await pub.publish(
        ErrorEvent(
            code=code,
            message=message,
            retryable=code not in NAO_RESOLVE_REPETINDO,
        )
    )


def _friendly(exc: LLMError) -> str:
    """Mensagem para o usuário, no padrão de §5.9.13.

    Diz o que aconteceu, o estado real e o próximo passo — nunca "ops,
    algo deu errado".
    """
    name = exc.provider or "o provedor"
    match exc.code:
        case "llm_auth_failed":
            return (
                f"A chave do {name} foi rejeitada. Confira em Configurações → Modelos."
            )
        case "llm_rate_limited":
            return (
                f"{name} limitou as requisições. Espere alguns segundos "
                "ou troque de modelo no seletor."
            )
        case "llm_timeout":
            return (
                f"{name} não respondeu. Tente de novo, ou troque de modelo "
                "— seu histórico é preservado."
            )
        case "llm_unavailable":
            return f"{name} está indisponível agora. Tente em instantes."
        case "llm_context_too_long":
            return (
                "A conversa ficou longa demais para este modelo. "
                "Comece uma conversa nova ou escolha um modelo com mais contexto."
            )
        case _:
            return str(exc)
