import copy

import numpy as np
import pytest

from msa.assets import model_for, validate_portfolio
from msa.contracts import Portfolio, Scenario
from msa.engine.calendar import Calendar
from msa.engine.pipeline import calculate
from msa.engine.scenarios import draw


def inputs(case):
    portfolio = Portfolio.model_validate(case["portfolio"])
    scenario = Scenario.model_validate(case["mock_scenario"])
    return portfolio, scenario, scenario.event_quote


def test_hand_calculated_constant_scenario(case):
    portfolio, scenario, text = inputs(case)
    for shock in scenario.shocks:
        shock.low = shock.high = shock.mode
    result = calculate(portfolio, scenario, text, 42, 128)
    a = result["assets"][0]["metrics"]
    # Независимый ручной расчёт: 1200 * .9 - 780 * 1.05 - 180 = 81.
    for quantile in ("p05", "p50", "p95"):
        assert a["ebitda"][quantile] == pytest.approx(81)
        assert a["interest"][quantile] == pytest.approx(90)
        assert a["icr"][quantile] == pytest.approx(0.9)
    assert a["ebitda"]["baseline"] == pytest.approx(240)
    assert result["assets"][0]["breach_probability"] == 1
    assert len(result["months"]) == 12


def test_reproducible_joint_portfolio_quantiles(case):
    portfolio, scenario, text = inputs(case)
    result = calculate(portfolio, scenario, text, 42, 1024)
    assert result == calculate(portfolio, scenario, text, 42, 1024)
    assert result["draws_sha256"] != calculate(portfolio, scenario, text, 43, 1024)["draws_sha256"]
    samples, _ = draw(scenario, 42, 1024)
    # Ожидаемый итог по исходным суммам, до вычисления квантилей.
    expected = 2800 * (1 + samples["revenue"]) - 1690 * (1 + samples["cost"]) - 490
    assert result["portfolio"]["metrics"]["ebitda"]["p05"] == pytest.approx(
        np.quantile(expected, 0.05)
    )
    sum_of_asset_p05 = sum(a["metrics"]["ebitda"]["p05"] for a in result["assets"])
    assert result["portfolio"]["metrics"]["ebitda"]["p05"] != pytest.approx(sum_of_asset_p05)
    assert 0 <= result["portfolio"]["breach_probability"] <= 1


def test_missing_inputs_invalid_shocks_and_negative_ebitda(case):
    portfolio, scenario, text = inputs(case)
    invalid = copy.deepcopy(portfolio)
    invalid.assets[0].inputs.pop("rate")
    with pytest.raises(ValueError, match="ровно поля"):
        validate_portfolio(invalid)
    scenario.shocks[0].low = -2
    with pytest.raises(ValueError, match="диапазона"):
        calculate(portfolio, scenario, text, 42, 128)
    model = model_for(portfolio.assets[0].asset_type)
    shocks = {"revenue": np.array([-0.8]), "cost": np.array([0.1]), "rate": np.array([0.02])}
    result = model.evaluate(portfolio.assets[0], shocks, Calendar(portfolio.as_of).day_counts())
    assert result.annual["icr"][0] < 0
    assert result.breach[0]


def test_no_duplicate_drivers_or_invented_quote(case):
    portfolio, scenario, text = inputs(case)
    scenario.event_quote = "Нет в исходном тексте"
    with pytest.raises(ValueError, match="Цитата"):
        calculate(portfolio, scenario, text, 42, 128)
    scenario.event_quote = text
    scenario.shocks[-1] = scenario.shocks[0]
    with pytest.raises(ValueError, match="ровно один"):
        calculate(portfolio, scenario, text, 42, 128)
