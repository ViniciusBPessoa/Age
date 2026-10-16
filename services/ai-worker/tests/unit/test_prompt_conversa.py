"""O system prompt como material de ensino (§5.9).

Um prompt ensina de duas formas ao mesmo tempo: pelo que ele MANDA e
pelo que ele DEMONSTRA. Quando as duas discordam, a demonstração é mais
forte, porque o modelo vê o texto inteiro como exemplo de escrita.

Foi o que aconteceu de verdade aqui. A seção de estilo proibia o
travessão em letras garrafais, e o resto do arquivo usava travessão em
quase toda linha de lista. O modelo obedecia ao exemplo, e as respostas
chegavam ao usuário cheias de travessão, com o relato "ele continua
usando travessão mesmo proibido".

Estes testes guardam a coerência entre a regra e o exemplo, e a
presença das instruções de condução de conversa que foram escritas
depois de medir o comportamento real com a chave de produção.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x@localhost/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("MASTER_KEY", "k" * 48)
os.environ.setdefault("SERVICE_JWT_SECRET", "s" * 48)

from app.prompts import build_system_prompt  # noqa: E402

TRAVESSAO = "—"
TRACO_LONGO = "–"


def _linhas_com(texto: str, char: str) -> list[str]:
    return [linha for linha in texto.split("\n") if char in linha]


class TestOPromptNaoEnsinaOQueProibe:
    def test_so_as_linhas_que_explicam_a_regra_usam_travessao(self) -> None:
        """O arquivo tinha 29 travessões enquanto proibia travessão.

        As únicas ocorrências legítimas são as que NOMEIAM o caractere
        para proibi-lo. Qualquer outra é exemplo de uso, e exemplo
        vence instrução.
        """
        prompt = build_system_prompt()
        suspeitas = [
            linha
            for linha in _linhas_com(prompt, TRAVESSAO)
            if "Proibido" not in linha
        ]

        assert suspeitas == [], (
            "o prompt usa travessão fora da regra que o proíbe; "
            f"linhas: {suspeitas}"
        )

    def test_nao_usa_traco_longo_em_lugar_nenhum(self) -> None:
        """O en dash é a saída fácil de quem foi proibido de usar em
        dash, e sai igual na tela da pessoa."""
        prompt = build_system_prompt()
        suspeitas = [
            linha
            for linha in _linhas_com(prompt, TRACO_LONGO)
            if "Proibido" not in linha
        ]

        assert suspeitas == []

    def test_a_proibicao_continua_escrita(self) -> None:
        """Contraprova do teste acima: ele passaria vazio se alguém
        apagasse a seção inteira em vez de corrigir os exemplos."""
        prompt = build_system_prompt()

        assert "Proibido o travessão" in prompt
        assert "dois-pontos" in prompt


class TestConducaoDeConversa:
    """As instruções escritas depois de medir o comportamento real.

    Cada uma corrige um defeito observado numa conversa de verdade com
    a chave de produção, e não uma suposição sobre o que o modelo faria.
    """

    def test_distingue_quem_executa_de_quem_quer_pensar_junto(self) -> None:
        """O defeito: tratar "quero aprender X" igual a "marca dentista
        quinta 15h". Um pede conversa, o outro pede ação."""
        prompt = build_system_prompt()

        assert "Duas pessoas usam o mesmo campo de texto" in prompt
        assert "Quem quer executar" in prompt
        assert "Quem quer pensar junto" in prompt

    def test_manda_responder_a_pergunta_antes_de_propor_agenda(self) -> None:
        """"Quero aprender machine learning, o que preciso antes" é uma
        pergunta sobre a matéria. Responder com blocos de agenda é
        responder outra coisa."""
        prompt = build_system_prompt()

        assert "Responda a pergunta de verdade" in prompt
        assert "Não proponha nada ainda" in prompt

    def test_combina_o_tamanho_antes_de_um_plano_grande(self) -> None:
        """Medido: 31 sessões e 24 horas propostas de uma vez para quem
        não tinha dito prazo nem ritmo."""
        prompt = build_system_prompt()

        assert "combine o tamanho" in prompt
        assert "8 sessões" in prompt
        # Duas opções concretas, não um "qual seu prazo?" em aberto.
        assert "Qual dos dois cabe na sua vida?" in prompt

    def test_proibe_anunciar_trabalho_nao_feito(self) -> None:
        """"Preparei o plano" dito antes de a ferramenta responder é a
        forma mais rápida de perder a confiança de quem confere."""
        prompt = build_system_prompt()

        assert "Não anuncie trabalho que você não fez" in prompt

    def test_trata_correcao_como_ajuste_e_nao_pedido_novo(self) -> None:
        """Medido: duas propostas de estudo da mesma matéria empilhadas
        na tela, porque a segunda não substituiu a primeira."""
        prompt = build_system_prompt()

        assert "Correção não é pedido novo" in prompt
        assert "mais cedo" in prompt


class TestOPromptSegueMontandoDireito:
    def test_o_fuso_entra_no_texto(self) -> None:
        """Guarda contra uma edição que quebre a interpolação: sem o
        fuso, toda data que o modelo calcula sai errada."""
        prompt = build_system_prompt(timezone="Europe/Lisbon")

        assert "Europe/Lisbon" in prompt

    def test_nao_sobrou_marcador_de_formatacao(self) -> None:
        """`{today}` literal no texto significa interpolação quebrada."""
        prompt = build_system_prompt()

        for marcador in ("{today}", "{weekday}", "{timezone}"):
            assert marcador not in prompt

    def test_nao_ha_barra_n_literal(self) -> None:
        """Uma edição por script já escreveu `\\n` literal aqui, e o
        texto chegou ao modelo com a barra visível no meio da frase."""
        prompt = build_system_prompt()

        assert "\\n" not in prompt
