"""O prompt ensina a oferecer a checklist e a NÃO escrevê-la em prosa.

A ferramenta sozinha não resolve o relato. `create_task` passou a aceitar
`cobre_eventos`, mas se o prompt não disser quando usar, o modelo repete
o que fez antes: escrever "Coberto na sessão 1, hoje 16h15" na observação
e deixar `task_event_links` vazia.

O cabeçalho de `test_prompt_conversa.py` registra a lição que vale aqui:
o prompt ensina pelo que MANDA e pelo que DEMONSTRA. Por isso estes
testes olham a substância da regra, não só a presença de uma frase.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x@localhost/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("MASTER_KEY", "k" * 48)
os.environ.setdefault("SERVICE_JWT_SECRET", "s" * 48)

from app.prompts import build_system_prompt  # noqa: E402

PROMPT = build_system_prompt()


def _secao(titulo: str) -> str:
    """O texto de uma seção `## ...` até a próxima."""
    partes = PROMPT.split(f"## {titulo}")
    assert len(partes) > 1, f"seção '{titulo}' não existe no prompt"
    return partes[1].split("\n## ")[0]


class TestAOferta:
    def test_a_secao_existe(self) -> None:
        """Sabotagem: apagar a seção inteira."""
        assert "Sessão de estudo pede checklist" in PROMPT

    def test_manda_oferecer_e_esperar_o_sim(self) -> None:
        """A oferta é pergunta, não iniciativa.

        Sem isto o modelo despeja 6 tarefas que ninguém pediu — ruído
        mesmo sem entrar na lista, porque alguém tem de revisar.
        """
        secao = _secao("Sessão de estudo pede checklist")

        assert "Faço isso?" in secao
        assert "não chame `create_task` no mesmo turno" in secao.lower()

    def test_diz_como_ligar_topico_a_sessao(self) -> None:
        secao = _secao("Sessão de estudo pede checklist")

        assert "cobre_eventos" in secao
        # A ordem importa: sessão 1 é o primeiro id devolvido.
        assert "ordem" in secao.lower()


class TestProibeAProsa:
    def test_proibe_escrever_o_vinculo_na_observacao(self) -> None:
        """O defeito relatado, nomeado no prompt.

        Sabotagem: tirar a regra 2. Este teste é o que cai.
        """
        secao = _secao("Sessão de estudo pede checklist")

        assert "Coberto na sessão 1" in secao, (
            "o prompt deve citar a prosa ERRADA de verdade; regra "
            "abstrata não ensina"
        )
        assert "prosa" in secao.lower()

    def test_topico_sem_horario_e_dito_em_voz_alta(self) -> None:
        # "*args ficou fora das 2h" tem de virar tarefa SEM vínculo, e
        # a pessoa tem de saber. Inventar horário mente sobre a agenda.
        secao = _secao("Sessão de estudo pede checklist")

        assert "SEM `cobre_eventos`" in secao


class TestNaoEhSoEstudo:
    def test_vale_para_qualquer_area_da_vida(self) -> None:
        """Pedido explícito: "não é só estudo, pode ser trabalho,
        academia, qualquer coisa da vida"."""
        secao = _secao("Sessão de estudo pede checklist")

        assert "não é só para estudo" in secao.lower()
        for area in ("Trabalho", "academia"):
            assert area.lower() in secao.lower()


class TestNaoDesfazOQueJaExistia:
    def test_a_secao_do_sentido_inverso_continua(self) -> None:
        """Contrapeso: as duas direções do vínculo convivem.

        A seção antiga cobre "já está na lista → ache um horário". Se a
        nova a tivesse substituído, `reserve_time_for_task` ficaria sem
        instrução.
        """
        assert "Reservar tempo para uma tarefa" in PROMPT
        assert "reserve_time_for_task" in PROMPT


class TestNaoPrometeOQueNaoExiste:
    """O Age prometia que marcar o bloco concluía a tarefa junto.

    `counterpart_of_event` existe em `task_store.py` e nunca foi
    chamada. Fui ligá-la e descobri que o buraco era maior: **concluir
    um evento não existe em lugar nenhum** — não há rota na API, botão
    na agenda nem ferramenta no worker. A função morta era metade de uma
    funcionalidade que nunca foi feita.

    Enquanto quase não havia vínculo a promessa passava despercebida.
    Com a checklist ligando todo plano de estudo, quem tentasse marcar o
    bloco concluiria que o sistema quebrou.
    """

    def test_nao_promete_marcar_o_bloco(self) -> None:
        """Sabotagem: devolver a frase antiga ao prompt."""
        proibido = "marcar o bloco como feito, a tarefa é concluída junto"

        assert proibido not in PROMPT

    def test_diz_o_que_de_fato_acontece(self) -> None:
        # O caminho que EXISTE é o contrário: concluir a tarefa faz o
        # Age lembrar do bloco e oferecer liberar o horário.
        secao = _secao("Reservar tempo para uma tarefa (§5.7.1)")

        assert "liberar" in secao.lower()

    def test_avisa_explicitamente_que_nao_existe(self) -> None:
        secao = _secao("Reservar tempo para uma tarefa (§5.7.1)")

        assert "não existe hoje" in secao
