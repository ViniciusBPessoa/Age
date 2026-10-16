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


    def test_proibe_dizer_que_propos_sem_chamar_a_ferramenta(self) -> None:
        """Medido: diante de "marca dentista quinta 15h", um turno leu a
        agenda, conferiu o horário livre e escreveu "propus quinta 22,
        das 15h às 16h". Nenhuma ferramenta de escrita foi chamada e
        não havia cartão nenhum para aprovar.

        A regra antiga falava de ferramenta que "falhou", e não cobria
        o caso de nem chamar.
        """
        prompt = build_system_prompt()

        assert "Achar o horário não é propor" in prompt
        assert '"staged": true' in prompt

    def test_manda_executar_pedido_direto_no_mesmo_turno(self) -> None:
        """O risco da regra acima é o modelo passar a perguntar sempre.
        "Marca X quinta 15h" tem o quê e o quando: devolver "quer que eu
        prepare?" é repetir a pergunta que a pessoa já respondeu."""
        prompt = build_system_prompt()

        assert "Pedido direto e completo é para executar" in prompt

    def test_manda_descartar_a_proposta_antiga_ao_corrigir(self) -> None:
        """Medido: "na verdade 16h, e só 30 min" depois de um dentista
        proposto às 15h deixou DOIS cartões de dentista na tela.

        Não existe ferramenta que edite proposta pendente, então
        corrigir é descartar e propor de novo, nessa ordem.
        """
        prompt = build_system_prompt()

        assert "DUPLICA" in prompt
        assert "discard_proposals" in prompt
        assert "title_contains" in prompt

    def test_pede_resposta_do_tamanho_da_pergunta(self) -> None:
        """Medido: um pedido de uma linha recebeu quatro parágrafos
        cobrindo quatro assuntos diferentes."""
        prompt = build_system_prompt()

        assert "Responda o que foi perguntado" in prompt

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


class TestRevisoesNaoEntramEscondidas:
    """`include_reviews` tem default `true`, e ninguém avisava o modelo.

    Medido na conta do usuário: ele pediu 3 blocos de 1h, segunda,
    quarta e sexta às 16h. A resposta na tela dizia "deixei as revisões
    curtas de fora para ficar exatamente nas 3 que você pediu", a
    chamada foi sem `include_reviews`, e o plano nasceu com 24 sessões
    às 8h15, incluindo sábado e domingo.

    O texto prometeu uma coisa e a ferramenta fez outra. É o mesmo
    defeito de "anunciar trabalho não feito", mas com a ferramenta
    trabalhando ESCONDIDO em vez de não trabalhar.
    """

    def test_avisa_que_o_default_acrescenta_revisoes(self) -> None:
        prompt = build_system_prompt()

        assert "include_reviews" in prompt
        # O número importa: "acrescenta revisões" não comunica que o
        # plano triplica.
        assert "vinte e quatro sessões" in prompt

    def test_manda_passar_false_quando_a_quantidade_foi_combinada(self) -> None:
        prompt = build_system_prompt()

        assert "include_reviews: false" in prompt
        assert "Quantidade combinada é quantidade final" in prompt

    def test_exige_respeitar_os_dias_e_o_horario_pedidos(self) -> None:
        """24 sessões às 8h15 quando o pedido era 16h. O horário não é
        detalhe de apresentação: é o que faz o plano caber na vida."""
        prompt = build_system_prompt()

        assert "não pode virar todo dia às 8h15" in prompt

    def test_oferece_as_revisoes_em_vez_de_impor(self) -> None:
        """Revisão espaçada ajuda de verdade, então a regra não é
        "nunca": é "não sem a pessoa saber"."""
        prompt = build_system_prompt()

        assert "9 sessões em vez de 3" in prompt
