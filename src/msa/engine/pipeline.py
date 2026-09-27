"""Выборка, расчёт и агрегация до вычисления квантилей, без знания типа актива."""

import numpy as np

from msa.assets import model_for, validate_portfolio, validate_scenario
from msa.contracts import Portfolio, Scenario
from msa.engine.calendar import Calendar
from msa.engine.scenarios import draw

MODEL_VERSION = "prod-2"


def quantiles(values):
    return dict(
        zip(
            ("p05", "p50", "p95"),
            np.quantile(values, [0.05, 0.5, 0.95], axis=0).tolist(),
            strict=True,
        )
    )


def summary(evaluation, baseline):
    return {
        "metrics": {
            name: {"baseline": float(baseline.annual[name][0]), **quantiles(values)}
            for name, values in evaluation.annual.items()
        },
        "monthly": {name: quantiles(values) for name, values in evaluation.monthly.items()},
        "breach_probability": float(evaluation.breach.mean()),
    }


def calculate(portfolio: Portfolio, scenario: Scenario, text: str, seed: int, points: int):
    validate_portfolio(portfolio)
    validate_scenario(scenario, portfolio, text)
    calendar = Calendar(portfolio.as_of)
    # Строка выборки задаёт один общий исход для всех активов; отдельные выборки
    # изменили бы зависимость между компаниями и риск портфеля.
    draws, digest = draw(scenario, seed, points)
    zero = {name: np.zeros(1) for name in draws}
    evaluations, baselines, assets = [], [], []
    for asset in portfolio.assets:
        model = model_for(asset.asset_type)
        evaluation = model.evaluate(asset, draws, calendar.day_counts())
        baseline = model.evaluate(asset, zero, calendar.day_counts())
        evaluations.append(evaluation)
        baselines.append(baseline)
        assets.append(
            {
                "asset_id": asset.asset_id,
                "name": asset.name,
                "source": asset.source,
                **summary(evaluation, baseline),
            }
        )
    # validate_portfolio гарантирует одно семейство и общий способ агрегации.
    model = model_for(portfolio.assets[0].asset_type)
    return {
        "model_version": MODEL_VERSION,
        "seed": seed,
        "points": points,
        "draws_sha256": digest,
        "as_of": portfolio.as_of.isoformat(),
        "months": [d.isoformat() for d in calendar.month_ends()],
        "assets": assets,
        # Квантили берём после сложения реализаций: сумма квантилей активов
        # в общем случае не равна квантилю портфеля.
        "portfolio": summary(model.aggregate(evaluations), model.aggregate(baselines)),
        "metric_definitions": model.metrics,
        "assumptions": [
            *model.assumptions,
            "Драйверы независимы и имеют треугольные распределения.",
            "P5/P50/P95 — квантили исходов.",
            "Общая матрица шоков; портфель агрегируется внутри каждой реализации.",
        ],
    }
