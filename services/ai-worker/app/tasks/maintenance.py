"""Jobs de manutenção: título de conversa e expurgo.

ARQUITETURA.md §6.2.2 e §6.2.4.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import text

from app.adapters.llm.registry import build_llm
from app.config import get_settings
from app.crypto import SecretBox, llm_context
from app.db import user_session
from app.ports.llm import LLMError, Message
from app.prompts import build_title_prompt

log = logging.getLogger("ai-worker.maintenance")


async def title_conversation(ctx: dict, payload: dict) -> dict:
    """Gera o título depois da primeira resposta completa (§6.2.2).

    Chamada curta e separada, máximo 8 tokens de saída. Falhar não afeta
    a conversa: o fallback é truncar a primeira mensagem do usuário.
    """
    conversation_id = UUID(payload["conversation_id"])
    user_id = UUID(payload["user_id"])
    settings = get_settings()

    async with user_session(user_id) as db:
        row = (
            await db.execute(
                text(
                    """
                    SELECT title, title_is_auto FROM core.conversations
                    WHERE id = :cid
                    """
                ),
                {"cid": str(conversation_id)},
            )
        ).first()

        if row is None:
            return {"status": "gone"}
        existing_title, is_auto = row

        # Renomeada pelo usuário: nunca sobrescreve escolha humana.
        if not is_auto:
            return {"status": "skipped", "reason": "renamed_by_user"}
        if existing_title:
            return {"status": "skipped", "reason": "already_titled"}

        first = (
            await db.execute(
                text(
                    """
                    SELECT content FROM core.messages
                    WHERE conversation_id = :cid AND role = 'user'
                      AND deleted_at IS NULL
                    ORDER BY created_at ASC LIMIT 1
                    """
                ),
                {"cid": str(conversation_id)},
            )
        ).first()

        if first is None or not first[0]:
            return {"status": "skipped", "reason": "no_user_message"}
        first_message = first[0]

        cred = (
            await db.execute(
                text(
                    """
                    SELECT provider_key, encrypted_api_key, default_model, base_url
                    FROM core.llm_credentials
                    WHERE user_id = :uid AND is_active = true
                    ORDER BY last_verified_at DESC NULLS LAST LIMIT 1
                    """
                ),
                {"uid": str(user_id)},
            )
        ).first()

    title = _fallback_title(first_message)
    source = "fallback"

    # Credencial do usuário se houver; senão a da instância (§3451), que
    # é o que faz o título gerado valer para quem nunca colou chave.
    #
    # **Sem checar cota, de propósito**, e por duas razões. Primeira: o
    # usuário não pediu este turno — ele é efeito colateral de mandar a
    # primeira mensagem, e cobrar cota por algo que a pessoa não escolheu
    # seria gastar o limite dela sem aviso. Segunda: é barato e limitado
    # a `max_tokens=16`, uma vez por conversa. O gasto segue registrado
    # em `turn_usage`, então aparece no relatório de custo mesmo não
    # descontando da cota.
    escolhida = None
    if cred is not None:
        provider_key, blob, model, base_url = cred
        try:
            escolhida = (
                provider_key,
                SecretBox(settings.master_key).decrypt(
                    bytes(blob), llm_context(user_id)
                ),
                model,
                base_url,
            )
        except Exception as exc:
            log.warning("falha ao decifrar credencial de %s: %s", user_id, exc)
    elif settings.has_instance_key:
        escolhida = (
            settings.instance_llm_provider,
            settings.instance_llm_api_key.strip(),
            settings.instance_llm_model.strip() or None,
            settings.instance_llm_base_url.strip() or None,
        )

    if escolhida is not None:
        provider_key, api_key, model, base_url = escolhida
        try:
            provider = build_llm(
                provider_key, api_key=api_key, model=model, base_url=base_url
            )
            try:
                completion = await provider.complete(
                    [Message(role="user", content=build_title_prompt(first_message))],
                    temperature=0.0,
                    max_tokens=16,
                )
                generated = (completion.message.content or "").strip().strip('"')
                # Primeira linha só: modelo às vezes devolve explicação
                # depois do título, apesar do prompt pedir o contrário.
                generated = generated.splitlines()[0].strip() if generated else ""
                # 60 caracteres cabem na sidebar sem truncar na UI.
                if generated and len(generated) <= 60:
                    title = generated
                    source = "llm"
                elif generated:
                    title = generated[:57] + "…"
                    source = "llm"
            finally:
                await provider.aclose()
        except (LLMError, Exception) as exc:  # noqa: BLE001
            log.info("título gerado por fallback (%s)", type(exc).__name__)

    # Grava como SUGESTAO no schema do worker. Aplicar em
    # `core.conversations` e do core-api, que e o dono daquele schema
    # (secao 2.3.2) - o worker so tem SELECT ali.
    async with user_session(user_id) as db:
        await db.execute(
            text(
                """
                INSERT INTO ai.title_suggestions
                  (conversation_id, user_id, title, source)
                VALUES (:cid, :uid, :title, :source)
                ON CONFLICT (conversation_id) DO UPDATE
                  SET title = EXCLUDED.title, source = EXCLUDED.source,
                      applied = false, created_at = now()
                """
            ),
            {
                "cid": str(conversation_id),
                "uid": str(user_id),
                "title": title,
                "source": source,
            },
        )

    return {"status": "done", "title": title, "source": source}


def _fallback_title(message: str, limit: int = 40) -> str:
    clean = " ".join(message.split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


async def purge_conversations(ctx: dict, payload: dict | None = None) -> dict:
    """Nao faz mais o expurgo: ele mora no core-api.

    O worker so tem SELECT em `core` (secao 2.3.2), e DELETE em
    `core.conversations` e escrita. Mantido como no-op para que o cron
    registrado continue valido; a limpeza real roda em
    `core-api/app/jobs/purge.py`.

    A regra de ouro do expurgo segue valendo onde ele acontece: apaga
    DIALOGO, nunca evento, tarefa, sessao de estudo ou rotina - o
    usuario aprovou aquilo (secao 6.2.3).
    """
    log.debug("purge_conversations e no-op no worker; ver core-api")
    return {"purged": 0, "delegated_to": "core-api"}
