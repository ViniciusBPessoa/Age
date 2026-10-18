"""Confirmar pela conversa, e as regras que mantêm isso seguro.

Até aqui o Age só sabia PROPOR. Não existia ferramenta de aplicar: para
confirmar era preciso clicar no cartão, e o relato foi que isso nunca
funcionava. Numa conversa fluida, "pode confirmar" tem de bastar.

`apply_proposals` abre esse caminho, e com ele vem um risco novo: é a
única ferramenta do worker que escreve na agenda de verdade. O que a
mantém honesta não é o código, é o prompt, porque decidir se houve
consentimento é leitura de texto. Por isso estes testes cobram do
prompt as travas que o código não tem como impor.

A §13 da ARQUITETURA.md previu a mudança: "o poder existe, com um
limite deliberado: ele propõe e o usuário aprova em um clique. Se isso
incomodar na prática, a mudança é pequena e localizada [...] é um
ajuste de política, não de arquitetura". O limite de §5.2 continua de
pé: nada entra na agenda sem o usuário aprovar, e o que muda é o canal
da aprovação, de clique para palavra.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x@localhost/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("MASTER_KEY", "k" * 48)
os.environ.setdefault("SERVICE_JWT_SECRET", "s" * 48)

# O caminho REAL do worker, nao os modulos de tool direto: e o
# `run_turn` que decide o que o modelo recebe. Importar a tool
# diretamente passaria mesmo se ela nunca chegasse ao modelo.
import app.tasks.run_turn  # noqa: E402, F401
from app.domain.tools import build_tool_specs  # noqa: E402
from app.prompts import build_system_prompt  # noqa: E402


def _prompt() -> str:
    return build_system_prompt(timezone="America/Sao_Paulo")


def _spec(nome: str):
    for spec in build_tool_specs():
        if spec.name == nome:
            return spec
    raise AssertionError(f"ferramenta {nome} não registrada")


class TestAFerramentaExiste:
    def test_apply_proposals_esta_registrada(self) -> None:
        assert _spec("apply_proposals").name == "apply_proposals"

    def test_a_descricao_lista_as_palavras_de_consentimento(self) -> None:
        """A decisão é de leitura, então a lista precisa estar à vista.

        Sem exemplos concretos, "quando a pessoa confirmar" é vago o
        bastante para o modelo decidir que um "boa" bastou.
        """
        descricao = _spec("apply_proposals").description.lower()
        for palavra in ["pode confirmar", "confirma", "fechou"]:
            assert palavra in descricao

    def test_a_descricao_proibe_iniciativa_propria(self) -> None:
        descricao = _spec("apply_proposals").description.lower()
        assert "nunca" in descricao
        assert "iniciativa" in descricao

    def test_confirm_blocking_nao_e_obrigatorio(self) -> None:
        """Conflito bloqueante não pode entrar por descuido de schema.

        Se o parâmetro fosse obrigatório, o modelo teria de mandar algum
        valor sempre, e mandar `true` por preenchimento automático é
        exatamente o acidente que não pode acontecer.
        """
        params = _spec("apply_proposals").parameters
        assert "confirm_blocking" not in params["required"]


class TestOPromptGuardaOConsentimento:
    def test_o_prompt_exige_palavra_explicita(self) -> None:
        texto = _prompt()
        assert "Confirmar é dela, não sua" in texto
        assert "apply_proposals" in texto

    def test_o_prompt_separa_pedido_direto_de_consentimento(self) -> None:
        """"Marca dentista quinta 15h" quer a proposta, não o fato.

        É o caso mais fácil de errar: o pedido é completo, soa decidido,
        e aplicar direto parece eficiência. Seria escrever na agenda sem
        a pessoa ver o que vai entrar.
        """
        texto = _prompt()
        assert "pedido direto não é consentimento" in texto.lower()

    def test_o_prompt_barra_a_satisfacao_como_autorizacao(self) -> None:
        # "Boa", "legal" são reações ao texto, não liberação.
        texto = _prompt().lower()
        assert "parece satisfeita" in texto
        assert "não é autorização" in texto or "não autorização" in texto

    def test_o_prompt_manda_relatar_o_que_entrou(self) -> None:
        texto = _prompt().lower()
        assert "o que entrou" in texto

    def test_o_prompt_proibe_insistir_no_conflito(self) -> None:
        """Repetir a chamada com `confirm_blocking` sem resposta seria
        transformar uma pergunta de segurança em formalidade."""
        texto = _prompt().lower()
        assert "não insista" in texto
        assert "confirm_blocking" in texto


class TestAsRegrasDeConversa:
    """As oito regras pedidas: "como seria uma conversa fluida"."""

    def test_manda_mostrar_o_raciocinio_antes_da_tabela(self) -> None:
        assert "Mostre o raciocínio em uma linha" in _prompt()

    def test_manda_dizer_o_que_ficou_de_fora(self) -> None:
        """Foi o pedido literal depois do plano cortado para 1h30: a
        pessoa quis saber como ficaria o novo recorte, e recebeu só o
        resultado."""
        assert "Diga o que ficou de fora" in _prompt()

    def test_manda_oferecer_a_alternativa_junto(self) -> None:
        assert "Ofereça a alternativa junto" in _prompt()

    def test_manda_fechar_com_pergunta(self) -> None:
        assert "Feche com uma pergunta" in _prompt()

    def test_trata_correcao_e_confirmacao_na_mesma_frase(self) -> None:
        """"pode, mas sexta deixa 1h" é corrigir E aprovar."""
        texto = _prompt()
        assert "Correção e confirmação na mesma frase" in texto

    def test_preve_confirmacao_parcial(self) -> None:
        assert "Confirmação parcial existe" in _prompt()

    def test_manda_dizer_o_que_da_quando_nao_da(self) -> None:
        texto = _prompt()
        assert "Quando não der, diga o que dá" in texto

    def test_as_regras_vem_com_exemplo(self) -> None:
        """Contrapeso dos testes acima.

        Afirmar só a presença do título deixaria passar uma seção com
        oito cabeçalhos e nenhuma frase. O prompt ensina mais pelo
        exemplo do que pela ordem, e foi assim que o travessão proibido
        continuou aparecendo nas respostas.
        """
        texto = _prompt()
        inicio = texto.index("## Como uma boa conversa funciona")
        fim = texto.index("## Não anuncie trabalho que você não fez")
        secao = texto[inicio:fim]

        assert len(secao) > 1200, "a seção virou lista de títulos"
        # Exemplos em voz de conversa, entre aspas, não paráfrase.
        assert secao.count('"') >= 8
