"""Duas regras que vieram de defeitos medidos na conta real.

Cada teste traz a sabotagem que precisa derrubá-lo. São asserções sobre
TEXTO do prompt — fracas por natureza, porque não provam que o modelo
obedece. O que elas garantem é que a regra não desaparece do arquivo
numa edição futura; a obediência eu confiro na tela.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x@localhost/x")

from app.prompts import build_system_prompt  # noqa: E402

PROMPT = build_system_prompt(timezone="America/Sao_Paulo")


class TestPedidoDiretoEParaFazer:
    """O caso: "separe cada dia em atividades todo e bote no sistema".

    O Age respondeu com a tabela e "Confirmo assim?" — ofereceu em vez
    de fazer. A regra que causava isso ("não chame `create_task` no
    mesmo turno em que oferece") existe por um motivo bom, mas disparava
    mesmo quando o pedido era explícito.
    """

    def test_a_excecao_existe(self) -> None:
        """Sabotagem: tirar a exceção, voltando à regra sem ressalva."""
        assert "pedido direto é para FAZER" in PROMPT

    def test_da_exemplos_do_que_conta_como_pedido(self) -> None:
        """Sabotagem: deixar só a frase abstrata.

        "Pedido direto" sozinho não distingue "me cria as tarefas" de
        "o que você acha de criar tarefas?". Os exemplos são o que
        tornam a regra aplicável.
        """
        for exemplo in ("bota no sistema", "cria os to-dos"):
            assert exemplo in PROMPT, exemplo

    def test_a_regra_original_continua(self) -> None:
        """Sabotagem: apagar a regra em vez de abrir exceção.

        Sem ela o Age volta a criar seis tarefas que ninguém pediu —
        o defeito que a regra existe para evitar. As duas precisam
        coexistir.
        """
        assert "não chame `create_task` no mesmo turno em que oferece" in PROMPT


class TestPesquisarEmVezDeLembrar:
    """A busca na web existia e o prompt não a mencionava.

    No turno dos filmes da Pixar foram **zero** chamadas a
    `/tools/search` (conferido no log do worker). Ele acertou de
    memória; o próximo erro passaria despercebido.
    """

    def test_manda_pesquisar_fato_verificavel(self) -> None:
        """Sabotagem: remover a seção inteira."""
        assert "web_search" in PROMPT
        assert "fato externo verificável" in PROMPT

    def test_lista_os_casos_em_que_vale(self) -> None:
        """Sabotagem: deixar "fato verificável" sem exemplo.

        Sem a lista, "verificável" é interpretação livre — e a ordem de
        lançamento de um filme não parece um fato de pesquisa até
        alguém errar.
        """
        for caso in ("edital", "ordem de lançamento", "preço"):
            assert caso in PROMPT, caso

    def test_manda_citar_a_fonte(self) -> None:
        """Sabotagem: tirar a instrução de citar.

        A lista de fontes na tela depende de o modelo escrever os links
        na resposta. Sem esta linha, a pesquisa acontece e a pessoa não
        vê de onde veio.
        """
        assert "cite as fontes" in PROMPT.lower()

    def test_diz_o_que_NAO_pesquisar(self) -> None:
        """Sabotagem: mandar pesquisar sempre.

        Buscar "o que é uma função em Python" gasta ~1.200 tokens e
        atrasa a resposta sem melhorar nada — e o histórico relê isso a
        cada mensagem seguinte.
        """
        assert "conhecimento estável" in PROMPT


class TestContagem:
    """O usuário pediu 3 filmes e a agenda ficou com 4 eventos."""

    def test_n_significa_n(self) -> None:
        """Sabotagem: remover a regra de contagem."""
        assert "N significa N" in PROMPT

    def test_manda_conferir_antes_de_afirmar(self) -> None:
        """Sabotagem: pedir o número certo sem mandar CONFERIR.

        O defeito não foi o modelo querer errar: foi afirmar que
        agendou sem contar o que agendou. É o mesmo padrão do vínculo
        que dizia ter feito e não fez.
        """
        assert "confira que a quantidade" in PROMPT

    def test_manda_dizer_em_vez_de_completar(self) -> None:
        """Sabotagem: deixar o modelo "arredondar" para cima.

        Criar um a mais parece zelo e vira item que ninguém pediu na
        agenda da pessoa.
        """
        assert "em vez de completar por conta própria" in PROMPT
