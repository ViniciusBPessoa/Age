"""Copia as saídas do turno de `ai` para `core`.

ARQUITETURA.md §2.3.2. Existe por causa da fronteira de propriedade de
schema: o worker produz a resposta do modelo, mas não pode escrever em
`core.messages` — ele só tem SELECT ali. Então ele grava em
`ai.turn_outputs`, e o core-api, que é dono de `core`, copia.

Isso não é duplicação de dado: `ai.turn_outputs` é fila de entrega, e
`core.messages` é o histórico canônico. A linha em `ai` fica marcada
`consumed = true` e é descartada junto com o turno.

O dreno é **idempotente** e roda na leitura (`GET /messages` e
`GET /conversations`). Não há job periódico para isso de propósito: o
dado só importa quando alguém vai ler, e drenar na leitura garante que
nunca se veja uma conversa incompleta.

A idempotência depende de `FOR UPDATE ... SKIP LOCKED` no SELECT, e
isso não é detalhe: sem o lock, duas leituras CONCORRENTES veem a
mesma linha com `consumed = false` e as duas copiam. No fim de um
turno a tela chama `/messages` e `/plans` quase juntas — medi 6
milissegundos de diferença na conta do usuário — e o resultado foi a
conversa dele com cada mensagem aparecendo DUAS vezes, pergunta e
resposta.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger("core-api.turn_sync")


async def drain_turn_outputs(
    db: AsyncSession, user_id: UUID, conversation_id: UUID | None = None
) -> int:
    """Copia respostas pendentes para `core.messages`.

    Devolve quantas mensagens foram copiadas. Zero é o caso normal.
    """
    # Dois escopos: um com alias (dentro do CTE) e um sem (no UPDATE,
    # que não tem FROM). Reaproveitar o mesmo texto nos dois causava
    # `missing FROM-clause entry for table "o"`.
    scope_aliased = "AND o.conversation_id = :cid" if conversation_id else ""
    scope_plain = "AND conversation_id = :cid" if conversation_id else ""
    params: dict[str, object] = {"uid": str(user_id)}
    if conversation_id:
        params["cid"] = str(conversation_id)

    # INSERT ... SELECT numa ida só: evita carregar as linhas para o
    # Python e torna a operação atômica.
    result = await db.execute(
        text(
            f"""
            WITH pending AS (
                SELECT o.id, o.conversation_id, o.role, o.content,
                       o.tool_calls, o.tool_call_id, o.provider_key,
                       o.model, o.latency_ms, o.created_at,
                       u.prompt_tokens, u.completion_tokens,
                       u.estimated_cost_usd
                FROM ai.turn_outputs o
                LEFT JOIN ai.turn_usage u ON u.turn_id = o.turn_id
                WHERE o.user_id = :uid AND o.consumed = false {scope_aliased}
                ORDER BY o.created_at, o.sequence
                FOR UPDATE OF o SKIP LOCKED
            ), inserted AS (
                INSERT INTO core.messages
                  (user_id, conversation_id, role, content, tool_calls,
                   tool_call_id, prompt_tokens, completion_tokens,
                   estimated_cost_usd, provider_key, model, latency_ms,
                   created_at)
                SELECT :uid, p.conversation_id, p.role, p.content,
                       p.tool_calls, p.tool_call_id, p.prompt_tokens,
                       p.completion_tokens, p.estimated_cost_usd,
                       p.provider_key, p.model,
                       p.latency_ms, p.created_at
                FROM pending p
                RETURNING conversation_id
            )
            SELECT count(*), array_agg(DISTINCT conversation_id)
            FROM inserted
            """
        ),
        params,
    )
    row = result.first()
    copied = row[0] if row else 0
    touched = (row[1] if row else None) or []

    # Antes do early-return, de propósito: a trilha de uso não depende
    # de haver mensagem nova para copiar. Quando este INSERT vivia lá
    # embaixo, um turno cuja mensagem já tinha sido drenada mas cujo uso
    # não ficava sem registro para sempre.
    await _copiar_uso(db, user_id)

    if not copied:
        return 0

    # Marca como consumido. Separado do INSERT porque o worker é dono
    # da tabela e o core-api só tem SELECT nela — então quem marca é...
    # também o core-api? Não: `ai.turn_outputs` tem UPDATE concedido ao
    # core_api especificamente para isto (ver migration 0005).
    await db.execute(
        text(
            f"""
            UPDATE ai.turn_outputs
            SET consumed = true
            WHERE user_id = :uid AND consumed = false {scope_plain}
            """
        ),
        params,
    )

    # Atualiza contador e recência das conversas afetadas.
    await db.execute(
        text(
            """
            UPDATE core.conversations c
            SET message_count = (
                    SELECT count(*) FROM core.messages m
                    WHERE m.conversation_id = c.id AND m.deleted_at IS NULL
                ),
                last_message_at = (
                    SELECT max(created_at) FROM core.messages m
                    WHERE m.conversation_id = c.id AND m.deleted_at IS NULL
                ),
                updated_at = now()
            WHERE c.id = ANY(:ids)
            """
        ),
        {"ids": [str(c) for c in touched]},
    )

    log.debug("drenadas %d mensagens para user=%s", copied, user_id)
    return copied


async def _copiar_uso(db: AsyncSession, user_id: UUID) -> None:
    """Copia `ai.turn_usage` para `core.usage_log`.

    `core.usage_log` é a trilha de auditoria no schema do core (§12). O
    painel de uso lê `ai.turn_usage` direto, porque ela tem `turn_id` e
    `used_instance_key` e esta cópia não — mas a trilha existe para que
    o core tenha o registro mesmo que o schema `ai` seja purgado.

    **A idempotência é do banco**, pela unique parcial em `turn_id`
    (migration 0012). Era um `NOT EXISTS` sobre
    `(user_id, created_at, model)`, e isso falhava por construção: num
    `INSERT ... SELECT` o `NOT EXISTS` lê o snapshot do início da
    instrução e não vê as linhas que a própria instrução insere, então
    dois turnos no mesmo `now()` com o mesmo modelo entravam os dois.
    Havia uma duplicata assim neste banco quando a 0012 foi escrita.

    Fica em função própria, e chamada ANTES do early-return de quem
    drena, porque antes ela vivia depois dele: um turno cuja mensagem já
    tinha sido drenada mas cujo uso não nunca era copiado, e nada
    reclamava.
    """
    await db.execute(
        text(
            """
            INSERT INTO core.usage_log
              (user_id, turn_id, provider_key, model, prompt_tokens,
               completion_tokens, estimated_cost_usd, created_at)
            SELECT u.user_id, u.turn_id, u.provider_key, u.model,
                   u.prompt_tokens, u.completion_tokens,
                   u.estimated_cost_usd, u.created_at
            FROM ai.turn_usage u
            WHERE u.user_id = :uid
              AND u.turn_id IS NOT NULL
            ON CONFLICT (turn_id) WHERE turn_id IS NOT NULL DO NOTHING
            """
        ),
        {"uid": str(user_id)},
    )


async def apply_pending_titles(db: AsyncSession, user_id: UUID) -> int:
    """Aplica TODAS as sugestoes de titulo pendentes do usuario.

    A versao por conversa so roda quando aquela conversa e aberta, e a
    barra lateral mostra a lista. Sem isto, quem manda a primeira
    mensagem ve "Sem titulo" ali ate sair e voltar na conversa - o
    titulo ja existia, faltava ser aplicado.

    Um UPDATE unico em vez de um por linha: a lista e o caminho mais
    quente da interface.
    """
    result = await db.execute(
        text(
            """
            WITH pending AS (
                SELECT conversation_id, title
                FROM ai.title_suggestions
                WHERE user_id = :uid AND applied = false
            ),
            updated AS (
                UPDATE core.conversations c
                SET title = p.title, updated_at = now()
                FROM pending p
                WHERE c.id = p.conversation_id
                  AND c.title IS NULL
                  AND c.title_is_auto = true
                RETURNING c.id
            )
            UPDATE ai.title_suggestions s
            SET applied = true
            FROM updated u
            WHERE s.conversation_id = u.id AND s.user_id = :uid
            RETURNING s.conversation_id
            """
        ),
        {"uid": str(user_id)},
    )
    return len(result.fetchall())


async def apply_title_suggestion(
    db: AsyncSession, user_id: UUID, conversation_id: UUID
) -> str | None:
    """Aplica o título que o worker sugeriu (§6.2.2).

    Só aplica se o título ainda é automático e está vazio — nunca
    sobrescreve escolha humana.
    """
    result = await db.execute(
        text(
            """
            WITH suggestion AS (
                SELECT title FROM ai.title_suggestions
                WHERE conversation_id = :cid AND user_id = :uid
                  AND applied = false
            )
            UPDATE core.conversations c
            SET title = (SELECT title FROM suggestion), updated_at = now()
            WHERE c.id = :cid
              AND c.title IS NULL
              AND c.title_is_auto = true
              AND EXISTS (SELECT 1 FROM suggestion)
            RETURNING c.title
            """
        ),
        {"cid": str(conversation_id), "uid": str(user_id)},
    )
    applied = result.scalar_one_or_none()

    if applied is not None:
        await db.execute(
            text(
                """
                UPDATE ai.title_suggestions SET applied = true
                WHERE conversation_id = :cid AND user_id = :uid
                """
            ),
            {"cid": str(conversation_id), "uid": str(user_id)},
        )
    return applied
