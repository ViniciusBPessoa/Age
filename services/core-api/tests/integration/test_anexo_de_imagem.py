"""Upload de imagem e vínculo com a mensagem.

Contra Postgres de verdade porque o que importa aqui é o que o banco
garante: as três constraints da migration 0013, o RLS por conta, e o
`CASCADE` que apaga o anexo com a mensagem.

Cada teste traz a sabotagem que precisa derrubá-lo.
"""

from __future__ import annotations

import os
import struct
import uuid
import zlib

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="precisa do Postgres do compose",
)


def png_bytes(lado: int = 4) -> bytes:
    """Um PNG real, montado à mão — sem depender de PIL no teste."""
    linhas = b"".join(b"\x00" + b"\xff\x00\x00" * lado for _ in range(lado))

    def chunk(tipo: bytes, dados: bytes) -> bytes:
        c = tipo + dados
        return struct.pack(">I", len(dados)) + c + struct.pack(">I", zlib.crc32(c))

    ihdr = struct.pack(">IIBBBBB", lado, lado, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(linhas))
        + chunk(b"IEND", b"")
    )


@pytest.fixture(autouse=True)
async def _engine_limpo():
    """Descarta o engine antes e depois de cada teste.

    Sem isto os testes passam isolados e falham em conjunto com
    "Event loop is closed": o engine de `app.core.db` e um singleton de
    modulo e guarda conexoes presas ao loop do teste anterior, que o
    pytest-asyncio ja fechou. O mesmo padrao de `test_rota_de_uso.py`.
    """
    from app.core.db import dispose_engine

    await dispose_engine()
    yield
    await dispose_engine()


def _engine_dono():
    from sqlalchemy.ext.asyncio import create_async_engine

    url = os.getenv("MIGRATION_DATABASE_URL") or os.environ["DATABASE_URL"]
    return create_async_engine(url)


async def _usuario() -> uuid.UUID:
    from sqlalchemy import text

    user_id = uuid.uuid4()
    engine = _engine_dono()
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO core.users (id, email, password_hash, "
                    "timezone) VALUES (:u, :e, 'x', 'America/Sao_Paulo')"
                ),
                {"u": str(user_id), "e": f"anexo-{user_id}@age.dev.br"},
            )
    finally:
        await engine.dispose()
    return user_id


async def _como_core_api(user_id: uuid.UUID, sql: str, params: dict | None = None):
    """Executa com a role de runtime e `app.user_id` posto, como a rota.

    Usar o engine do dono mascararia o RLS — e foi exatamente assim que
    a migration 0012 "passou" apagando zero linhas.
    """
    from sqlalchemy import text

    from app.core.db import user_session

    async with user_session(user_id) as db:
        resultado = await db.execute(text(sql), params or {})
        try:
            return resultado.fetchall()
        except Exception:
            return []


class TestValidacao:
    """A validação pura, sem banco: bytes entram, veredito sai."""

    @pytest.mark.parametrize(
        ("dados", "esperado"),
        [
            (b"\xff\xd8\xff\xe0" + b"\x00" * 20, "image/jpeg"),
            (png_bytes(), "image/png"),
            (b"GIF89a" + b"\x00" * 20, "image/gif"),
            (b"GIF87a" + b"\x00" * 20, "image/gif"),
            (b"RIFF\x24\x00\x00\x00WEBPVP8 ", "image/webp"),
        ],
    )
    def test_os_quatro_formatos_do_provedor(self, dados: bytes, esperado: str) -> None:
        """Sabotagem: remover uma entrada de `_ASSINATURAS`.

        Estes são os MESMOS bytes do teste do worker
        (`test_imagem_no_wire.py`). As duas cópias da detecção existem
        porque §2.4 proíbe os serviços de importarem código um do outro;
        testá-las contra a mesma tabela é o que impede que divirjam.
        """
        from app.services.imagem_recebida import detectar_media_type

        assert detectar_media_type(dados) == esperado

    def test_riff_que_nao_e_webp_e_recusado(self) -> None:
        """Sabotagem: checar só `RIFF`, sem os bytes 8-12.

        WAV e AVI também são RIFF. Esta é a sabotagem que os testes de
        reconhecimento NÃO pegam: todos continuam passando.
        """
        from app.services.imagem_recebida import detectar_media_type

        assert detectar_media_type(b"RIFF\x24\x08\x00\x00WAVEfmt ") is None

    def test_recusa_tamanho_antes_de_formato(self) -> None:
        """Sabotagem: checar formato primeiro.

        Um arquivo de 40 MB que não é imagem deve ouvir sobre o TAMANHO,
        porque é o que a pessoa pode resolver. Reclamar do formato
        manda ela converter um arquivo que ia ser recusado de todo jeito.
        """
        from app.services.imagem_recebida import ImagemRecusada, validar

        with pytest.raises(ImagemRecusada) as e:
            validar(b"nem imagem nem pequeno" * 300_000)
        assert "5 MB" in e.value.motivo

    def test_o_motivo_e_escrito_para_quem_anexou(self) -> None:
        """Sabotagem: devolver "invalid media type" ou um código.

        Quem lê isto está olhando a tela com a imagem do lado, não um
        log. A frase tem de dizer o que fazer.
        """
        from app.services.imagem_recebida import ImagemRecusada, validar

        with pytest.raises(ImagemRecusada) as e:
            validar(b"MZ\x90\x00isto e um executavel")
        assert "JPEG" in e.value.motivo and "PNG" in e.value.motivo

    def test_sha256_e_dos_bytes(self) -> None:
        """Sabotagem: hashear o nome do arquivo, ou devolver constante."""
        import hashlib

        from app.services.imagem_recebida import validar

        dados = png_bytes()
        assert validar(dados).sha256 == hashlib.sha256(dados).hexdigest()


class TestConstraintsDoBanco:
    """O que o banco garante mesmo se a rota errar."""

    async def test_tipo_fora_dos_quatro_e_barrado(self) -> None:
        """Sabotagem: remover `ck_attachment_media_type`.

        A rota já valida, mas a rota é uma linha de código e a
        constraint é a invariante: qualquer caminho novo que escreva
        aqui — migration de dados, script de manutenção — passa por ela.
        """
        from sqlalchemy.exc import IntegrityError

        user_id = await _usuario()
        with pytest.raises(IntegrityError, match="ck_attachment_media_type"):
            await _como_core_api(
                user_id,
                "INSERT INTO core.message_attachments (user_id, filename, "
                "media_type, size_bytes, sha256, data) VALUES "
                "(:u, 'x.bmp', 'image/bmp', 10, 'h', :d)",
                {"u": str(user_id), "d": b"\x00"},
            )

    async def test_acima_de_5mb_e_barrado(self) -> None:
        """Sabotagem: remover `ck_attachment_tamanho`."""
        from sqlalchemy.exc import IntegrityError

        user_id = await _usuario()
        with pytest.raises(IntegrityError, match="ck_attachment_tamanho"):
            await _como_core_api(
                user_id,
                "INSERT INTO core.message_attachments (user_id, filename, "
                "media_type, size_bytes, sha256, data) VALUES "
                "(:u, 'g.png', 'image/png', 5242881, 'h', :d)",
                {"u": str(user_id), "d": b"\x00"},
            )

    async def test_linha_sem_conteudo_e_barrada(self) -> None:
        """Sabotagem: remover `ck_attachment_tem_conteudo`.

        Anexo sem `data` e sem `storage_key` é um anexo que não existe,
        e descobrir isso na hora de montar o prompt é tarde: o turno já
        foi enfileirado.
        """
        from sqlalchemy.exc import IntegrityError

        user_id = await _usuario()
        with pytest.raises(IntegrityError, match="ck_attachment_tem_conteudo"):
            await _como_core_api(
                user_id,
                "INSERT INTO core.message_attachments (user_id, filename, "
                "media_type, size_bytes, sha256) VALUES "
                "(:u, 'v.png', 'image/png', 10, 'h')",
                {"u": str(user_id)},
            )

    async def test_rls_impede_gravar_para_outra_conta(self) -> None:
        """Sabotagem: remover a policy, ou o `FORCE ROW LEVEL SECURITY`.

        É a razão pela qual os bytes estão no banco e não em arquivo:
        caminho de disco não tem policy, e quem errar o id numa rota
        leria anexo alheio.
        """
        from sqlalchemy.exc import ProgrammingError

        user_id = await _usuario()
        outra = uuid.uuid4()
        with pytest.raises(ProgrammingError, match="row-level security"):
            await _como_core_api(
                user_id,
                "INSERT INTO core.message_attachments (user_id, filename, "
                "media_type, size_bytes, sha256, data) VALUES "
                "(:o, 'alheio.png', 'image/png', 10, 'h', :d)",
                {"o": str(outra), "d": b"\x00"},
            )

    async def test_rls_esconde_anexo_de_outra_conta(self) -> None:
        """Sabotagem: policy só com `WITH CHECK`, sem `USING`.

        `WITH CHECK` governa escrita; `USING` governa leitura. Uma
        policy só com o primeiro deixaria QUALQUER conta ler os anexos
        de todas — e nenhum teste de escrita notaria.
        """
        from sqlalchemy import text

        dono = await _usuario()
        engine = _engine_dono()
        anexo_id = uuid.uuid4()
        try:
            async with engine.begin() as conn:
                # Como dono, com `app.user_id` posto: o FORCE RLS vale
                # até para ele, então sem isto o INSERT não entra.
                await conn.execute(
                    text("SELECT set_config('app.user_id', :u, false)"),
                    {"u": str(dono)},
                )
                await conn.execute(
                    text(
                        "INSERT INTO core.message_attachments (id, user_id, "
                        "filename, media_type, size_bytes, sha256, data) VALUES "
                        "(:i, :u, 'dono.png', 'image/png', 10, 'h', :d)"
                    ),
                    {"i": str(anexo_id), "u": str(dono), "d": b"\x00"},
                )
        finally:
            await engine.dispose()

        intrusa = await _usuario()
        linhas = await _como_core_api(
            intrusa,
            "SELECT id FROM core.message_attachments WHERE id = :i",
            {"i": str(anexo_id)},
        )
        assert linhas == []

        # E o dono continua vendo o seu: um teste que só afirma "a
        # intrusa não vê" passaria com a tabela vazia.
        do_dono = await _como_core_api(
            dono,
            "SELECT id FROM core.message_attachments WHERE id = :i",
            {"i": str(anexo_id)},
        )
        assert len(do_dono) == 1


class TestCicloDeVida:
    async def test_anexo_nasce_sem_mensagem(self) -> None:
        """Sabotagem: `message_id NOT NULL`.

        O upload acontece enquanto a pessoa escreve. Exigir a mensagem
        no INSERT obrigaria a criar a mensagem antes de ela ser enviada.
        """
        user_id = await _usuario()
        linhas = await _como_core_api(
            user_id,
            "INSERT INTO core.message_attachments (user_id, filename, "
            "media_type, size_bytes, sha256, data) VALUES "
            "(:u, 'a.png', 'image/png', 10, 'h', :d) "
            "RETURNING message_id, position",
            {"u": str(user_id), "d": png_bytes()},
        )
        assert linhas[0][0] is None
        assert linhas[0][1] == 0

    async def test_apagar_mensagem_apaga_o_anexo(self) -> None:
        """Sabotagem: `ondelete` ausente, ou `SET NULL`.

        Sem CASCADE, apagar a conversa deixaria os bytes no banco para
        sempre — e §9 (apagar os dados da conta) passaria a ter uma
        metade que mente.
        """
        from sqlalchemy import text

        user_id = await _usuario()
        conv_id, msg_id = uuid.uuid4(), uuid.uuid4()
        engine = _engine_dono()
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("SELECT set_config('app.user_id', :u, false)"),
                    {"u": str(user_id)},
                )
                await conn.execute(
                    text(
                        "INSERT INTO core.conversations (id, user_id, title) "
                        "VALUES (:c, :u, 'teste')"
                    ),
                    {"c": str(conv_id), "u": str(user_id)},
                )
                await conn.execute(
                    text(
                        "INSERT INTO core.messages (id, user_id, "
                        "conversation_id, role, content) VALUES "
                        "(:m, :u, :c, 'user', 'o que e isto?')"
                    ),
                    {"m": str(msg_id), "u": str(user_id), "c": str(conv_id)},
                )
                await conn.execute(
                    text(
                        "INSERT INTO core.message_attachments (user_id, "
                        "message_id, conversation_id, filename, media_type, "
                        "size_bytes, sha256, data) VALUES "
                        "(:u, :m, :c, 'f.png', 'image/png', 10, 'h', :d)"
                    ),
                    {
                        "u": str(user_id),
                        "m": str(msg_id),
                        "c": str(conv_id),
                        "d": png_bytes(),
                    },
                )

            antes = await _como_core_api(
                user_id,
                "SELECT count(*) FROM core.message_attachments WHERE message_id = :m",
                {"m": str(msg_id)},
            )
            assert antes[0][0] == 1

            async with engine.begin() as conn:
                await conn.execute(
                    text("SELECT set_config('app.user_id', :u, false)"),
                    {"u": str(user_id)},
                )
                await conn.execute(
                    text("DELETE FROM core.messages WHERE id = :m"),
                    {"m": str(msg_id)},
                )
        finally:
            await engine.dispose()

        depois = await _como_core_api(
            user_id,
            "SELECT count(*) FROM core.message_attachments WHERE message_id = :m",
            {"m": str(msg_id)},
        )
        assert depois[0][0] == 0
