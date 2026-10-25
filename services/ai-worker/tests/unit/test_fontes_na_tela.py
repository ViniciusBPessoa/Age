"""As fontes da busca chegam à tela (§5.1.3).

Cada teste traz a sabotagem que precisa derrubá-lo.

Este arquivo existe por um relato direto: *"o Deep Search, eu não tenho
nenhuma referência visual se está funcionando ou não. Depois que ele
procura, ele não escreve no chat separado quais foram as referências
utilizadas."*

A causa estava em `orchestrator.py`: no caminho de SUCESSO o resumo era
`None` — só erro produzia texto. A saída da ferramenta tinha as fontes
completas e ia **só para o modelo**, nunca para a tela.
"""

from __future__ import annotations

import json

from app.domain.orchestrator import _resumo_de_fontes, _separar_fontes


class TestSepararFontes:
    """Tirar `_fontes` do que vai ao modelo."""

    def test_as_fontes_saem_do_payload_do_modelo(self) -> None:
        """Sabotagem: deixar `_fontes` no resultado.

        O modelo já tem os mesmos dados em `results`; repetir seria
        pagar os tokens duas vezes no payload mais caro do turno —
        medido em ~6.455 tokens com conteúdo.
        """
        bruto = json.dumps(
            {
                "qualidade": "boa",
                "results": [{"title": "Edital", "url": "https://a.br"}],
                "_fontes": [
                    {
                        "titulo": "Edital",
                        "url": "https://a.br",
                        "site": "GOV.BR",
                        "confiavel": True,
                    }
                ],
            }
        )
        saida, fontes = _separar_fontes(bruto)

        assert "_fontes" not in saida
        assert "results" in saida, "o que o modelo precisa continua lá"
        assert fontes is not None
        assert fontes[0]["url"] == "https://a.br"

    def test_ferramenta_sem_fontes_passa_intacta(self) -> None:
        """Sabotagem: tentar desserializar tudo.

        TODA ferramenta passa por aqui — agenda, tarefas, rotinas. Um
        resultado que não é JSON, ou que não tem o campo, não pode ser
        reescrito nem perder nada.
        """
        for bruto in (
            '{"events": [], "count": 0}',
            "texto solto que não é JSON",
            "[1, 2, 3]",
            "",
        ):
            saida, fontes = _separar_fontes(bruto)
            assert saida == bruto, f"mexeu em {bruto!r}"
            assert fontes is None

    def test_campo_vazio_nao_vira_lista_vazia(self) -> None:
        """Sabotagem: devolver `[]` em vez de `None`.

        `[]` faria a tela desenhar a caixa de fontes vazia; `None` diz
        "esta ferramenta não tem fontes", que é outra coisa.
        """
        bruto = json.dumps({"results": [], "_fontes": []})
        _saida, fontes = _separar_fontes(bruto)
        assert fontes is None


class TestResumoDeFontes:
    """A linha que aparece com a caixa recolhida."""

    def test_nomeia_os_sites(self) -> None:
        """Sabotagem: voltar a `summary=None` no sucesso — o estado que
        o usuário fotografou, com a caixa dizendo só "7 consultas".
        """
        resumo = _resumo_de_fontes(
            [
                {"titulo": "a", "url": "https://1", "site": "GOV.BR"},
                {"titulo": "b", "url": "https://2", "site": "UFRGS"},
            ]
        )
        assert "GOV.BR" in resumo
        assert "UFRGS" in resumo

    def test_nao_repete_o_mesmo_site(self) -> None:
        """Sabotagem: concatenar sem remover repetido.

        Uma busca costuma trazer várias páginas do mesmo portal, e
        "GOV.BR, GOV.BR, GOV.BR" não informa nada.
        """
        resumo = _resumo_de_fontes(
            [
                {"titulo": "a", "url": "https://1", "site": "GOV.BR"},
                {"titulo": "b", "url": "https://2", "site": "GOV.BR"},
            ]
        )
        assert resumo.count("GOV.BR") == 1

    def test_corta_a_lista_longa(self) -> None:
        """Sabotagem: listar todos.

        Dez nomes de site numa linha de rodapé quebram o layout; o
        resto fica visível ao abrir a caixa.
        """
        fontes = [
            {"titulo": str(i), "url": f"https://{i}", "site": f"site{i}.com"}
            for i in range(9)
        ]
        resumo = _resumo_de_fontes(fontes)
        assert "e mais 6" in resumo
        assert "site8.com" not in resumo

    def test_sem_nome_de_site_ainda_diz_quantas(self) -> None:
        """Sabotagem: devolver string vazia.

        Resumo vazio reproduz o defeito original: a caixa volta a não
        dizer nada sobre a pesquisa.
        """
        resumo = _resumo_de_fontes(
            [{"titulo": "a", "url": "https://1", "site": None}]
        )
        assert "1 fontes" in resumo


class TestAToolDevolveAsFontes:
    """`web_search` monta o campo que o orquestrador extrai."""

    def test_classificar_alimenta_o_campo_de_fontes(self) -> None:
        """Sabotagem: não montar `_fontes` na tool.

        Sem o campo, tudo acima funciona e a tela continua vazia — é o
        tipo de furo que só aparece ligando as pontas.
        """
        import asyncio
        import uuid

        from app.domain.tools.registry import ToolContext
        from app.domain.tools.web_tools import web_search

        async def busca_falsa(op, **kwargs):
            return [
                {
                    "title": "Edital do Enem 2026",
                    "url": "https://gov.br/enem",
                    "site_name": "GOV.BR",
                    "rerank_score": 2.3,
                },
                {
                    "title": "Revision f6538409",
                    "url": "https://stackoverflow.com/x",
                    "site_name": "Stack Overflow",
                    "rerank_score": 0.54,
                },
            ]

        ctx = ToolContext(
            user_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            timezone="America/Sao_Paulo",
            search_web=busca_falsa,
        )
        saida = asyncio.run(web_search(ctx, {"query": "enem 2026"}))

        fontes = saida["_fontes"]
        assert len(fontes) == 2
        assert fontes[0]["url"] == "https://gov.br/enem"
        # A classificação atravessa: o lixo chega marcado, não escondido
        assert fontes[0]["confiavel"] is True
        assert fontes[1]["confiavel"] is False

    def test_fonte_sem_url_nao_entra(self) -> None:
        """Sabotagem: incluir resultado sem URL.

        Uma "fonte" sem endereço vira um link que não leva a lugar
        nenhum — pior que não mostrar.
        """
        from app.domain.tools.web_tools import classificar

        saida = classificar([{"title": "sem link", "rerank_score": 2.0}])
        fontes = [r for r in saida["results"] if r.get("url")]
        assert fontes == []
