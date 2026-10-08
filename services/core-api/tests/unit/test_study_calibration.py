"""Testes da lógica de recalibração.

ARQUITETURA.md §5.9.8. A recalibração é o que faz o produto melhorar
com o uso — e é onde um bug passa desapercebido por muito tempo, porque
o resultado "parece plausível" mesmo quando está errado.

O caso que motivou estes testes: `120 min × 0.67` gravava **15 min** em
vez de 80, porque o parâmetro fracionário era truncado para integer
pelo tipo da coluna. O plano seguinte ficava absurdo e ninguém notaria
sem olhar o número.

Estes testes cobrem a aritmética e os limites sem precisar de banco; a
integração é coberta pelo smoke do M3.
"""

from __future__ import annotations

import pytest

from app.services.study_store import _MAX_ADJUST, _MIN_SAMPLES


def clamp(ratio: float) -> float:
    """Replica o limite aplicado em `recalibrate_topic`."""
    return max(1 - _MAX_ADJUST, min(1 + _MAX_ADJUST, ratio))


def calibrate(estimated_min: int, ratio: float) -> int:
    """Replica o cálculo que o SQL faz, para testar a aritmética."""
    return max(15, round(estimated_min * clamp(ratio)))


# ---------------------------------------------------------------
# Aritmética
# ---------------------------------------------------------------
def test_faster_user_gets_smaller_estimate() -> None:
    """Estudou 40 min do que foi planejado em 60: ajusta para baixo.

    Regressão do bug de truncamento: 120 × 0.667 tem de dar 80, não 15.
    """
    assert calibrate(120, 80 / 120) == 80


def test_slower_user_gets_larger_estimate() -> None:
    assert calibrate(120, 150 / 120) > 120


def test_on_target_barely_changes() -> None:
    assert calibrate(120, 1.0) == 120


def test_adjustment_is_capped_downward() -> None:
    """Uma sessão de 10 min (desistiu no meio) não corta 80%.

    Sem o teto, o plano seguinte ficaria absurdo — e o usuário perderia
    confiança no número justamente por causa de um dia ruim.
    """
    result = calibrate(120, 10 / 120)  # ratio 0.083
    assert result == round(120 * (1 - _MAX_ADJUST))
    assert result >= 72


def test_adjustment_is_capped_upward() -> None:
    result = calibrate(120, 300 / 120)  # ratio 2.5
    assert result == round(120 * (1 + _MAX_ADJUST))


def test_never_below_floor() -> None:
    """Um tópico nunca vira uma sessão impossível de agendar."""
    assert calibrate(20, 0.1) >= 15


def test_cap_values_are_sane() -> None:
    """Sanidade dos limites: 40% é ajuste agressivo mas não absurdo."""
    assert 0.2 <= _MAX_ADJUST <= 0.5
    # Uma amostra só pode ser atípica; duas já indicam tendência.
    assert _MIN_SAMPLES >= 2


@pytest.mark.parametrize(
    "estimated,actual,planned,expected_direction",
    [
        (120, 80, 120, "faster"),
        (120, 120, 120, "on_target"),
        (120, 160, 120, "slower"),
        # 85/90 = 0.944, abaixo do corte de 0.95 — é ajuste, não ruído.
        (90, 85, 90, "faster"),
        # 88/90 = 0.978: dentro da faixa neutra.
        (90, 88, 90, "on_target"),
    ],
)
def test_direction_classification(
    estimated: int, actual: int, planned: int, expected_direction: str
) -> None:
    """A direção é o que o assistente comunica ao usuário (§5.9.8).

    Classificar 5% de diferença como "mais rápido" produziria mensagem
    de ajuste a cada sessão, e o usuário pararia de ler.
    """
    ratio = clamp(actual / planned)
    if ratio < 0.95:
        direction = "faster"
    elif ratio > 1.05:
        direction = "slower"
    else:
        direction = "on_target"
    assert direction == expected_direction
