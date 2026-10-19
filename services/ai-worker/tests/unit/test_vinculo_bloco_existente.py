"""Ligar to-do a bloco que JÁ ESTÁ na agenda (§5.7.1).

`test_checklist_ligada.py` cobre o sentido "propus as sessões agora,
ligo as tarefas a elas no mesmo turno". Este arquivo cobre o que
faltava, e que foi medido na conta de verdade:

    turno 1  o usuário aprova 2 blocos de finanças
    turno 2  "Tu tem como criar alguns tudulos vinculados a esses
             horários"
    resposta "Montei 8 itens, todos ligados ao bloco em que cada um
             cabe"
    banco    links = [] nas oito, zero linhas em task_event_links

No turno 2 os blocos já eram linhas do banco, não itens empilhados, e
`cobre_eventos` só aceita id de item DO MESMO TURNO. O modelo não tinha
nenhum caminho, e descreveu a intenção como se fosse resultado.

Daí duas coisas aqui: um namespace separado para id real de evento, e
a devolução explícita do que a ferramenta recusou, para o modelo não
poder afirmar vínculo que não existe.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x@localhost/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("MASTER_KEY", "k" * 48)
os.environ.setdefault("SERVICE_JWT_SECRET", "s" * 48)

from app.staging import StagingBuilder  # noqa: E402

TZ = "-03:00"
BLOCO_HOJE = "11111111-1111-4111-8111-111111111111"
BLOCO_AMANHA = "22222222-2222-4222-8222-222222222222"


def _builder() -> StagingBuilder:
    return StagingBuilder(user_id=uuid4(), conversation_id=uuid4())


def _item(b: StagingBuilder, item_id: str):
    return next(i for i in b.items if str(i.id) == str(item_id))


class TestEmpilhar:
    def test_bloco_existente_vai_para_event_refs(self) -> None:
        """Sabotagem: `stage_task_create` ignorar `eventos_existentes`.

        É literalmente o defeito: o parâmetro não existia, então o
        vínculo não tinha onde entrar.
        """
        b = _builder()
        tid = b.stage_task_create(
            title="Levantar gastos fixos",
            reason="cabe no bloco de hoje",
            tema="Planilha financeira",
            eventos_existentes=[BLOCO_HOJE],
        )
        assert _item(b, tid).event_refs == [BLOCO_HOJE]

    def test_nao_entra_em_links(self) -> None:
        """O teste que protege a semântica de aprovação.

        `links` forma o grupo de aprovação por fechamento transitivo: um
        bloco já aplicado ali dentro faria a tela oferecer aprovar e
        reverter o que já é fato na agenda.

        Sabotagem: chamar `self.link()` para o bloco existente.
        """
        b = _builder()
        tid = b.stage_task_create(
            title="Montar as abas",
            reason="cabe no bloco",
            eventos_existentes=[BLOCO_HOJE],
        )
        assert _item(b, tid).links == []

    def test_os_dois_caminhos_convivem_sem_se_misturar(self) -> None:
        """Uma tarefa pode cobrir um bloco proposto E um já existente.

        Sabotagem: funilar os dois parâmetros na mesma lista. Aí um id
        de banco iria para `links` (e o grupo de aprovação arrastaria um
        evento aplicado) ou um id de item iria para `event_refs` (e o
        core-api não acharia o evento).
        """
        b = _builder()
        sessao = b.stage_create(
            title="Sessão nova",
            start=f"2026-10-25T16:00:00{TZ}",
            end=f"2026-10-25T17:00:00{TZ}",
            reason="sessão",
        )
        tid = b.stage_task_create(
            title="Tópico",
            reason="cobre as duas",
            cobre_eventos=[sessao],
            eventos_existentes=[BLOCO_HOJE],
        )
        item = _item(b, tid)
        assert item.links == [sessao], "o par proposto se perdeu"
        assert item.event_refs == [BLOCO_HOJE], "o bloco existente se perdeu"

    def test_id_repetido_nao_duplica_o_vinculo(self) -> None:
        b = _builder()
        tid = b.stage_task_create(
            title="Tópico",
            reason="x",
            eventos_existentes=[BLOCO_HOJE, BLOCO_HOJE],
        )
        assert _item(b, tid).event_refs == [BLOCO_HOJE]

    def test_oito_tarefas_em_dois_blocos(self) -> None:
        """O caso de finanças: cada tarefa no bloco em que cabe."""
        b = _builder()
        ids = []
        for n in range(8):
            alvo = BLOCO_HOJE if n < 4 else BLOCO_AMANHA
            ids.append(
                b.stage_task_create(
                    title=f"item {n}",
                    reason="divisão combinada",
                    tema="Planilha financeira",
                    eventos_existentes=[alvo],
                )
            )
        refs = [_item(b, i).event_refs[0] for i in ids]
        assert refs.count(BLOCO_HOJE) == 4
        assert refs.count(BLOCO_AMANHA) == 4

    def test_sem_vinculo_continua_sem_vinculo(self) -> None:
        """Contrapeso: tarefa sem bloco não ganha `event_refs` nenhum."""
        b = _builder()
        tid = b.stage_task_create(title="Solta", reason="só anotar")
        assert _item(b, tid).event_refs == []
        assert _item(b, tid).links == []


class TestLigarTarefaQueJaExiste:
    def test_stage_task_link_produz_um_item_de_link(self) -> None:
        """`LINK`, e não `UPDATE`.

        Sabotagem: empilhar `UPDATE`. O core-api levanta "UPDATE de
        tarefa sem nenhuma mudança" quando não há campo alterado, e o
        item seria descartado na promoção com um `log.info` — a mesma
        falha silenciosa que esta mudança conserta.
        """
        b = _builder()
        tid = b.stage_task_link(
            task_id="aaaa-tarefa",
            eventos_existentes=[BLOCO_HOJE],
            reason="vou fazer nesse horário",
        )
        item = _item(b, tid)
        assert item.op == "LINK"
        assert item.target == "task"
        assert item.payload["task_id"] == "aaaa-tarefa"
        assert item.event_refs == [BLOCO_HOJE]
        assert item.links == []

    def test_uma_tarefa_em_varios_blocos(self) -> None:
        b = _builder()
        tid = b.stage_task_link(
            task_id="t1",
            eventos_existentes=[BLOCO_HOJE, BLOCO_AMANHA],
            reason="divide em dois dias",
        )
        assert _item(b, tid).event_refs == [BLOCO_HOJE, BLOCO_AMANHA]


class TestSerializacao:
    def test_to_json_leva_event_refs(self) -> None:
        """Sabotagem: esquecer o campo em `to_json`.

        O rascunho é o único canal entre worker e core-api. Campo que
        não entra aqui não existe para a promoção, e o vínculo morreria
        em silêncio exatamente como antes.
        """
        import json

        b = _builder()
        b.stage_task_create(
            title="Tópico", reason="x", eventos_existentes=[BLOCO_HOJE]
        )
        bruto = json.loads(b.to_json())
        assert bruto[0]["event_refs"] == [BLOCO_HOJE]

    def test_resumo_conta_o_vinculo(self) -> None:
        """O cartão diz que vai ligar algo, em vez de parecer vazio."""
        b = _builder()
        b.stage_task_link(
            task_id="t1", eventos_existentes=[BLOCO_HOJE], reason="x"
        )
        assert "ligar a um horário" in b.summary()


# ---------------------------------------------------------------
# A ferramenta: conferir e RECUSAR em voz alta
# ---------------------------------------------------------------
def _ctx(builder, achados):
    """Contexto com uma agenda falsa que devolve `achados`."""
    from app.domain.tools.registry import ToolContext

    async def read_calendar(operation, **kwargs):
        assert operation == "resolve_events"
        return achados

    return ToolContext(
        user_id=uuid4(),
        conversation_id=uuid4(),
        timezone="America/Sao_Paulo",
        plan_builder=builder,
        read_calendar=read_calendar,
    )


def _achado(ref: str, *, found=True, fim: datetime | None = None) -> dict:
    termina = fim or (datetime.now(UTC) + timedelta(hours=3))
    return {
        "id": ref,
        "kind": "local_events" if found else None,
        "title": "Bloco" if found else None,
        "start": (termina - timedelta(hours=1)).isoformat(),
        "end": termina.isoformat(),
        "found": found,
    }


class TestFerramentaConfere:
    async def test_id_bom_entra_e_volta_em_ligados(self) -> None:
        from app.domain.tools.task_tools import create_task

        b = _builder()
        out = await create_task(
            _ctx(b, [_achado(BLOCO_HOJE)]),
            {
                "title": "Levantar gastos",
                "reason": "x",
                "eventos_existentes": [BLOCO_HOJE],
            },
        )
        assert out["staged"] is True
        assert out["eventos_ligados"] == [BLOCO_HOJE]
        assert out["eventos_recusados"] == []
        assert "instruction" not in out

    async def test_id_que_nao_existe_e_RECUSADO_com_instrucao(self) -> None:
        """A metade que deixava o Age mentir.

        Sem devolver o recusado e sem mandar dizer em voz alta, o modelo
        resume "todos ligados" porque foi o que ele quis fazer.

        Sabotagem: empilhar o vínculo sem conferir, ou devolver só os
        aceitos.
        """
        from app.domain.tools.task_tools import create_task

        b = _builder()
        out = await create_task(
            _ctx(b, [_achado("inventado", found=False)]),
            {
                "title": "Pagar a fatura",
                "reason": "x",
                "eventos_existentes": ["inventado"],
            },
        )
        # A tarefa continua valendo: o usuário pediu a tarefa.
        assert out["staged"] is True
        assert out["eventos_ligados"] == []
        assert out["eventos_recusados"] == [
            {"id": "inventado", "motivo": "não existe na sua agenda"}
        ]
        assert "Pagar a fatura" in out["instruction"]
        # E nada foi empilhado como vínculo.
        assert b.items[0].event_refs == []

    async def test_bloco_que_ja_terminou_e_recusado(self) -> None:
        """Sabotagem: tirar a checagem de fim no passado.

        Ligar a bloco encerrado faria a lista dizer "1h reservada" para
        uma hora que já passou.
        """
        from app.domain.tools.task_tools import create_task

        b = _builder()
        out = await create_task(
            _ctx(
                b,
                [
                    _achado(
                        BLOCO_HOJE,
                        fim=datetime.now(UTC) - timedelta(hours=2),
                    )
                ],
            ),
            {
                "title": "Tarde demais",
                "reason": "x",
                "eventos_existentes": [BLOCO_HOJE],
            },
        )
        assert out["eventos_ligados"] == []
        assert out["eventos_recusados"][0]["motivo"] == "esse bloco já terminou"

    async def test_parte_boa_parte_ruim(self) -> None:
        """Um bloco serve, outro não: liga um e avisa do outro."""
        from app.domain.tools.task_tools import create_task

        b = _builder()
        out = await create_task(
            _ctx(
                b,
                [_achado(BLOCO_HOJE), _achado(BLOCO_AMANHA, found=False)],
            ),
            {
                "title": "Meio a meio",
                "reason": "x",
                "eventos_existentes": [BLOCO_HOJE, BLOCO_AMANHA],
            },
        )
        assert out["eventos_ligados"] == [BLOCO_HOJE]
        assert len(out["eventos_recusados"]) == 1
        assert "instruction" in out
        assert b.items[0].event_refs == [BLOCO_HOJE]

    async def test_sem_eventos_existentes_nao_mexe_na_resposta(self) -> None:
        """Contrapeso: quem não usa o recurso não vê campo novo."""
        from app.domain.tools.task_tools import create_task

        b = _builder()
        out = await create_task(
            _ctx(b, []), {"title": "Simples", "reason": "x"}
        )
        assert out["staged"] is True
        assert "eventos_ligados" not in out
        assert "eventos_recusados" not in out


class TestFerramentaDeLigar:
    async def test_liga_tarefa_existente(self) -> None:
        from app.domain.tools.task_tools import link_task_to_event

        b = _builder()
        out = await link_task_to_event(
            _ctx(b, [_achado(BLOCO_HOJE)]),
            {
                "task_id": "t1",
                "eventos_existentes": [BLOCO_HOJE],
                "reason": "vou fazer nesse horário",
            },
        )
        assert out["staged"] is True
        assert out["eventos_ligados"] == [BLOCO_HOJE]
        assert b.items[0].op == "LINK"

    async def test_nenhum_bloco_serve_nao_empilha_nada(self) -> None:
        """Cartão que aplica e não muda nada é pior que recusa clara.

        Sabotagem: empilhar o item mesmo sem nenhum bloco válido.
        """
        from app.domain.tools.task_tools import link_task_to_event

        b = _builder()
        out = await link_task_to_event(
            _ctx(b, [_achado("x", found=False)]),
            {
                "task_id": "t1",
                "eventos_existentes": ["x"],
                "reason": "y",
            },
        )
        assert out["staged"] is False
        assert b.items == []
        assert "instruction" in out


class TestRegistro:
    def test_a_ferramenta_esta_registrada(self) -> None:
        """Sabotagem: esquecer o import que registra o módulo.

        Ferramenta que existe no arquivo e não no registro é ferramenta
        que o modelo nunca vê.
        """
        import app.worker  # noqa: F401
        from app.domain.tools.registry import registered_names

        assert "link_task_to_event" in registered_names()

    def test_create_task_declara_eventos_existentes(self) -> None:
        """O spec é o que o modelo lê. Sem o campo, ele não o usa."""
        import app.worker  # noqa: F401
        from app.domain.tools.registry import build_tool_specs

        specs = {s.name: s for s in build_tool_specs()}
        props = specs["create_task"].parameters["properties"]
        assert "eventos_existentes" in props
        assert "cobre_eventos" in props
        # E a nova aparece com os dois campos obrigatórios.
        ligar = specs["link_task_to_event"].parameters
        assert set(ligar["required"]) == {
            "task_id",
            "eventos_existentes",
            "reason",
        }

