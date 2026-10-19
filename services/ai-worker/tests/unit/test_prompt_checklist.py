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


class TestEscolherOMecanismo:
    """Três caminhos de vínculo, e o modelo tem de acertar qual.

    O defeito medido na conta de verdade: os 2 blocos de finanças foram
    aprovados num turno; no seguinte o usuário pediu to-dos vinculados a
    eles; o Age respondeu "montei 8 itens, todos ligados ao bloco em que
    cada um cabe" e não ligou nada. `cobre_eventos` não alcança bloco de
    turno passado, e nada no prompt dizia o que fazer nesse caso.
    """

    SECAO = "Sessão de estudo pede checklist (§5.7.1)"

    def test_cita_os_tres_mecanismos(self) -> None:
        """Sabotagem: documentar só `cobre_eventos`.

        Com um caminho só descrito, o modelo usa aquele, ele falha
        calado, e a resposta afirma vínculo que não existe.
        """
        secao = _secao(self.SECAO)

        for mecanismo in (
            "cobre_eventos",
            "eventos_existentes",
            "link_task_to_event",
        ):
            assert mecanismo in secao, f"falta {mecanismo}"

    def test_diz_o_criterio_de_escolha(self) -> None:
        """Não basta listar: tem de dizer QUANDO cada um serve.

        O critério é um só: o bloco já foi aprovado ou não.
        """
        secao = _secao(self.SECAO).lower()

        assert "já está na agenda" in secao
        assert "neste turno" in secao

    def test_manda_pegar_o_id_real_em_get_events(self) -> None:
        """Sem isto o modelo não sabe de ONDE vem o id real."""
        secao = _secao(self.SECAO)

        assert "get_events" in secao

    def test_avisa_que_diff_item_id_antigo_nao_serve(self) -> None:
        """O erro natural: reusar o id da proposta do turno anterior.

        Sabotagem: tirar o aviso. Os dois são strings parecidas, e nada
        na forma denuncia a confusão.
        """
        secao = _secao(self.SECAO)

        assert "diff_item_id" in secao
        assert "turno passado" in secao

    def test_registra_o_caso_medido_com_a_frase_que_falhou(self) -> None:
        """O prompt deste projeto ensina pelo caso concreto.

        Sabotagem: trocar o relato pela regra abstrata. O cabeçalho de
        `test_prompt_conversa.py` registra por que isso não funciona.
        """
        secao = _secao(self.SECAO)

        assert "todos ligados ao bloco em que cada um cabe" in secao
        assert "Nada estava ligado" in secao


class TestNuncaAfirmarVinculoRecusado:
    """A metade que fazia o defeito ser invisível.

    A ferramenta passou a devolver `eventos_ligados` e
    `eventos_recusados`. Se o prompt não mandar olhar, o modelo resume
    "todos ligados" porque foi o que ele quis fazer, e a pessoa confia.
    """

    SECAO = "Sessão de estudo pede checklist (§5.7.1)"

    def test_manda_olhar_o_que_a_ferramenta_devolveu(self) -> None:
        secao = _secao(self.SECAO)

        assert "eventos_ligados" in secao
        assert "eventos_recusados" in secao

    def test_proibe_afirmar_o_que_foi_recusado(self) -> None:
        """Sabotagem: tirar a proibição.

        É a regra que impede exatamente a resposta que o usuário
        recebeu.
        """
        secao = _secao(self.SECAO).lower()

        assert "não está ligado" in secao
        assert "em voz alta" in secao

    def test_demonstra_como_dizer(self) -> None:
        """Manda nomear a tarefa, e mostra a frase.

        Regra sem exemplo gera "algumas tarefas ficaram sem bloco", que
        não diz QUAL e não serve para conferir.
        """
        secao = _secao(self.SECAO)

        assert "nomeando a tarefa" in secao
        assert "ficou na lista sem bloco" in secao


class TestSemTravessaoNoTextoNovo:
    def test_a_secao_nao_usa_travessao(self) -> None:
        """O prompt proíbe travessão e ENSINA pelo exemplo.

        Uma seção que usa o que ela proíbe treina o contrário do que
        manda. Já aconteceu nesta mesma seção, numa rodada anterior.
        """
        secao = _secao("Sessão de estudo pede checklist (§5.7.1)")

        assert "—" not in secao
