"""O anexo entrando no histórico que vai para o modelo.

`_to_domain_message` e `_carregar_anexos` não tinham teste nenhum antes
desta mudança — a suíte inteira passou verde depois de eu alterar a
assinatura de `_to_domain_message`, o que é exatamente o sinal de que
ninguém a exercitava.

Cada teste traz a sabotagem que precisa derrubá-lo.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

# O import dos adaptadores é o que os REGISTRA: `@register_llm` roda na
# importação do módulo, e `worker.py` faz exatamente este import com
# `noqa: F401`. Sem ele o registry está vazio e `model_sees_images`
# devolve `False` para tudo — foi o que este arquivo pegou primeiro.
from app.adapters.llm import deepseek, mock, openai  # noqa: F401
from app.adapters.llm.registry import model_sees_images
from app.ports.llm import ImagePart, TextPart
from app.tasks.run_turn import _to_domain_message

PNG = b"\x89PNG\r\n\x1a\nfake"
JPEG = b"\xff\xd8\xff\xe0fake"


def linha(message_id, role="user", content="o que e isto?", tool_calls=None, tcid=None):
    """Uma linha de `core.messages` como a query a devolve."""
    return (message_id, role, content, tool_calls, tcid)


class TestMensagemSemAnexo:
    def test_texto_puro_continua_string(self) -> None:
        """Sabotagem: sempre montar tupla de partes.

        Mensagem sem anexo tem de sair com `content` STRING: é o
        caminho de 100% das conversas de hoje, e transformá-lo em lista
        invalidaria o cache de prompt (50x mais barato) em todas elas.
        """
        msg = _to_domain_message(linha(uuid4()), {})
        assert msg.content == "o que e isto?"
        assert isinstance(msg.content, str)

    def test_sem_o_dicionario_de_anexos_tambem_funciona(self) -> None:
        """Sabotagem: `anexos_por_mensagem` sem default.

        O parâmetro é opcional de propósito; se algum chamador antigo
        passar só a linha, tem de continuar funcionando em vez de
        levantar `TypeError` dentro do worker.
        """
        msg = _to_domain_message(linha(uuid4()))
        assert msg.content == "o que e isto?"

    def test_tool_call_sobrevive(self) -> None:
        """Sabotagem: esquecer `tool_calls` ao montar a mensagem nova.

        A desempacotagem da linha ganhou um campo (`id`) no início; se
        os índices escorregarem, as tool calls do histórico somem e o
        modelo perde o rastro do que já executou.
        """
        bruto = [{"id": "c1", "name": "read_calendar", "arguments": {"dia": "hoje"}}]
        msg = _to_domain_message(
            linha(uuid4(), role="assistant", content=None, tool_calls=bruto), {}
        )
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0].name == "read_calendar"
        assert msg.tool_calls[0].arguments == {"dia": "hoje"}

    def test_tool_call_id_sobrevive(self) -> None:
        """Sabotagem: a mesma troca de índices, no último campo."""
        msg = _to_domain_message(
            linha(uuid4(), role="tool", content="resultado", tcid="c1"), {}
        )
        assert msg.tool_call_id == "c1"
        assert msg.role == "tool"


class TestMensagemComAnexo:
    def test_o_texto_vem_antes_da_imagem(self) -> None:
        """Sabotagem: `partes.extend(anexos)` antes do texto.

        A pergunta é o que diz ao modelo o que olhar na imagem, e o
        provedor lê a sequência na ordem em que ela chega. Imagem antes
        da pergunta transforma "quanto custa isto?" em descrição solta.
        """
        mid = uuid4()
        anexos = {str(mid): [ImagePart(data=PNG, media_type="image/png")]}
        msg = _to_domain_message(linha(mid), anexos)

        assert isinstance(msg.content, tuple)
        assert isinstance(msg.content[0], TextPart)
        assert msg.content[0].text == "o que e isto?"
        assert isinstance(msg.content[1], ImagePart)

    def test_varias_imagens_mantem_a_ordem_recebida(self) -> None:
        """Sabotagem: perder a ordem (um `set`, ou ordenar por tipo).

        "Compare a primeira com a segunda" muda de significado se a
        ordem embaralhar, e a ordem que vale é a que a pessoa escolheu
        no composer — garantida pelo `ORDER BY position` da consulta.
        """
        mid = uuid4()
        anexos = {
            str(mid): [
                ImagePart(data=PNG, media_type="image/png"),
                ImagePart(data=JPEG, media_type="image/jpeg"),
            ]
        }
        msg = _to_domain_message(linha(mid, content="compare as duas"), anexos)

        assert isinstance(msg.content, tuple)
        tipos = [p.media_type for p in msg.content[1:]]
        assert tipos == ["image/png", "image/jpeg"]

    def test_anexo_de_outra_mensagem_nao_vaza(self) -> None:
        """Sabotagem: ignorar o `message_id` e colar todos os anexos.

        Esta é a que nenhum teste de "a imagem chega" pega: colar todos
        os anexos da conversa em toda mensagem faria o modelo reanalisar
        imagens antigas em cada turno — resposta errada E conta
        multiplicada, porque cada imagem custa até 994 tokens.
        """
        minha, alheia = uuid4(), uuid4()
        anexos = {str(alheia): [ImagePart(data=PNG, media_type="image/png")]}
        msg = _to_domain_message(linha(minha), anexos)

        assert msg.content == "o que e isto?"
        assert isinstance(msg.content, str)

    def test_imagem_sem_texto_nao_cria_parte_vazia(self) -> None:
        """Sabotagem: `partes.append(TextPart(text=content))` sem checar.

        `content` nulo viraria `TextPart(text=None)`, que o adaptador
        mandaria como `{"type": "text", "text": null}` — e o provedor
        recusa com 400 depois de o upload já ter sido feito.
        """
        mid = uuid4()
        anexos = {str(mid): [ImagePart(data=PNG, media_type="image/png")]}
        msg = _to_domain_message(linha(mid, content=None), anexos)

        assert isinstance(msg.content, tuple)
        assert len(msg.content) == 1
        assert isinstance(msg.content[0], ImagePart)


class TestModeloQueVeImagem:
    """A consulta que decide se vale buscar bytes do banco."""

    def test_deepseek_flash_ve_imagem(self) -> None:
        """Sabotagem: devolver `False` sempre.

        Medido contra a API real em 20/10/2026: o `deepseek-flash`
        aceita imagem e cobra até 994 tokens por ela. `False` aqui faria
        o anexo ser silenciosamente ignorado — a pessoa manda a foto, o
        modelo responde sem tê-la visto, e nada no caminho reclama.
        """
        assert model_sees_images("deepseek", "deepseek-flash") is True
        assert model_sees_images("deepseek", "deepseek-v4-pro") is True

    def test_modelo_antigo_cai_no_fallback(self) -> None:
        """Sabotagem: devolver `False` para modelo fora da tabela.

        `deepseek-chat` continua aceito pela API por redirecionamento, e
        uma conversa antiga tem esse nome gravado em
        `core.conversations.model`.
        """
        assert model_sees_images("deepseek", "deepseek-chat") is True
        assert model_sees_images("deepseek", "nome-que-nao-existe") is True

    def test_provedor_desconhecido_e_false(self) -> None:
        """Sabotagem: levantar exceção, ou devolver `True`.

        `True` traria os bytes do banco para serem descartados; exceção
        derrubaria o turno inteiro por causa de um provedor mal
        configurado. `False` é o único que degrada direito.
        """
        assert model_sees_images("provedor-inexistente", "qualquer") is False

    def test_nao_pergunta_pelo_nome_do_provedor(self) -> None:
        """A função lê `supports_vision`, não compara `provider_key`.

        Sabotagem: trocar o corpo por `return key == "deepseek"`. Isto
        passaria em todos os testes acima e violaria §3.1.2 — e o
        primeiro provedor sem visão a entrar receberia imagem.
        """
        import ast
        import inspect
        import textwrap

        from app.adapters.llm import registry

        fonte = textwrap.dedent(inspect.getsource(registry.model_sees_images))
        arvore = ast.parse(fonte)

        # Só o CORPO, sem a docstring: ela cita `== "deepseek"` como
        # exemplo do que não fazer, e uma busca por texto no fonte
        # inteiro acusaria a própria explicação. Afirmar sobre a árvore
        # é o que distingue o código do comentário sobre o código.
        funcao = arvore.body[0]
        assert isinstance(funcao, ast.FunctionDef)
        corpo = funcao.body[1:] if ast.get_docstring(funcao) else funcao.body

        literais = {
            no.value
            for stmt in corpo
            for no in ast.walk(stmt)
            if isinstance(no, ast.Constant) and isinstance(no.value, str)
        }
        assert "deepseek" not in literais, (
            f"nome de provedor no corpo da funcao: {literais}"
        )

        nomes = {
            no.attr
            for stmt in corpo
            for no in ast.walk(stmt)
            if isinstance(no, ast.Attribute)
        }
        assert "supports_vision" in nomes


@pytest.mark.parametrize("vazio", [{}, None])
def test_dicionario_vazio_nao_quebra(vazio) -> None:
    """Sabotagem: `anexos_por_mensagem[str(message_id)]` com índice cru.

    `KeyError` aqui derrubaria todo turno de toda conversa sem anexo,
    que é o caminho comum.
    """
    msg = _to_domain_message(linha(uuid4()), vazio)
    assert msg.content == "o que e isto?"
