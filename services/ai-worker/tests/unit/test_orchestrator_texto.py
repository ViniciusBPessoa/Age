"""A junção do texto das várias iterações do laço (§5.1).

Um turno com ferramenta produz texto em MAIS DE UMA iteração: o modelo
fala, pede a ferramenta, recebe o resultado e volta a falar. O
orquestrador junta esses pedaços, e a junção era `"".join(...)`.

Numa resposta real, isso virou:

    "Antes de qualquer coisa, deixa eu ver o que tem lá.Olhei de 16/10
     a 16/11: sua agenda está vazia"

Repare o `lá.Olhei`. O usuário leu isso na tela, e o relato foi sobre o
texto estar mal formatado.

O defeito é do tipo que nenhum teste de provedor pegaria: cada pedaço
estava certo, só a costura entre eles estava errada.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x@localhost/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("MASTER_KEY", "k" * 48)
os.environ.setdefault("SERVICE_JWT_SECRET", "s" * 48)

from app.domain.orchestrator import _juntar_segmentos  # noqa: E402


def test_separa_os_pedacos_de_iteracoes_diferentes() -> None:
    """O caso exato que chegou ao usuário."""
    junto = _juntar_segmentos(
        [
            "Antes de qualquer coisa, deixa eu ver o que tem lá.",
            "Olhei de 16/10 a 16/11: sua agenda está vazia.",
        ]
    )

    assert "lá.Olhei" not in junto
    assert "lá.\n\nOlhei" in junto


def test_nao_duplica_quebra_quando_o_modelo_ja_formatou() -> None:
    """Se o pedaço anterior já termina em quebra de linha, acrescentar
    outra abriria um buraco de três linhas no meio da resposta.

    O modelo costuma terminar um parágrafo com `\\n\\n` antes de chamar
    a ferramenta, então este é o caminho comum, não a exceção.
    """
    junto = _juntar_segmentos(["Primeiro parágrafo.\n\n", "Segundo."])

    assert junto == "Primeiro parágrafo.\n\nSegundo."
    assert "\n\n\n" not in junto


def test_ignora_pedacos_vazios() -> None:
    """Uma iteração que só chamou ferramenta não produz texto.

    Sem o filtro, ela viraria um separador solto e abriria linha em
    branco no começo ou no meio da resposta.
    """
    assert _juntar_segmentos(["", "Olhei sua agenda.", ""]) == (
        "Olhei sua agenda."
    )
    assert _juntar_segmentos(["", ""]) == ""
    assert _juntar_segmentos([]) == ""


def test_um_pedaco_so_passa_intacto() -> None:
    """O caso mais comum de todos: turno sem ferramenta. Não deve ganhar
    nenhum caractere."""
    assert _juntar_segmentos(["Sua agenda está vazia."]) == (
        "Sua agenda está vazia."
    )


def test_tres_iteracoes_viram_tres_paragrafos() -> None:
    """Turno com duas rodadas de ferramenta, que é o normal quando o
    modelo consulta agenda e depois rotinas."""
    junto = _juntar_segmentos(["Deixa eu ver.", "Achei 3 eventos.", "Pronto."])

    assert junto == "Deixa eu ver.\n\nAchei 3 eventos.\n\nPronto."
    assert junto.count("\n\n") == 2
