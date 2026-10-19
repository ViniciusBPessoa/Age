"""A mensagem depois de confirmar pela conversa nunca sai vazia.

Achado no PRIMEIRO teste de ponta a ponta da rota
`/internal/calendar/apply-proposals`, nao na leitura do codigo: um item
que falhou no meio da aplicacao devolvia

    {"applied": 0, "message": "Nada entrou: "}

com dois pontos e nada depois. O Age receberia isso e nao teria o que
contar a pessoa, que e justamente o defeito que esta rodada inteira
existe para consertar: falha que nao se explica.

A causa era a coleta de motivos olhar so `refused_upfront`. Recusa de
pre-voo (conflito bloqueante) preenche `failed`; falha no meio da
escrita tambem, e essa nao estava sendo lida.
"""

from __future__ import annotations

from app.api.calendar import _mensagem_do_apply


class TestAlgoEntrou:
    def test_conta_o_que_entrou(self) -> None:
        assert "2" in _mensagem_do_apply(2, [])

    def test_sucesso_ignora_motivos_antigos(self) -> None:
        # Aplicacao parcial: o que entrou e o que importa na abertura.
        assert _mensagem_do_apply(1, ["algum motivo"]).startswith("1 item")


class TestNadaEntrou:
    def test_diz_o_motivo_quando_ha_um(self) -> None:
        msg = _mensagem_do_apply(0, ["conflito bloqueante: choca com Prova"])
        assert "choca com Prova" in msg

    def test_junta_ate_tres_motivos(self) -> None:
        motivos = ["primeiro", "segundo", "terceiro", "quarto"]
        msg = _mensagem_do_apply(0, motivos)

        assert "primeiro; segundo; terceiro" in msg
        # Letra solta como motivo ("d") casaria dentro de "entrou": os
        # motivos aqui sao palavras inteiras de proposito.
        assert "quarto" not in msg, "quatro motivos viram parede de texto"

    def test_sem_motivo_ainda_assim_explica(self) -> None:
        """O caso do defeito.

        Sabotagem que precisa derruba-lo: voltar ao
        `"Nada entrou: " + "; ".join(recusados)` sem o ramo final.
        """
        msg = _mensagem_do_apply(0, [])

        assert msg.strip() != "Nada entrou:"
        assert not msg.rstrip().endswith(":"), (
            "mensagem terminando em dois pontos promete um motivo que nao vem"
        )
        assert len(msg) > 20
