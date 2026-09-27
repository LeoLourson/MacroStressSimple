"""Общий реестр типов активов, финансовых подписей и правил проверки."""

from msa.contracts import Portfolio, Scenario

from .base import AssetModel
from .corporate import CorporateLoan

MODELS: dict[str, AssetModel] = {CorporateLoan.asset_type: CorporateLoan()}


def model_for(asset_type: str) -> AssetModel:
    if asset_type not in MODELS:
        raise ValueError(f"Неизвестный тип актива: {asset_type}")
    return MODELS[asset_type]


def catalog() -> list[dict]:
    return [
        dict(
            asset_type=m.asset_type,
            label=m.label,
            fields=m.fields,
            drivers=m.drivers,
            metrics=m.metrics,
            assumptions=m.assumptions,
        )
        for m in MODELS.values()
    ]


def validate_portfolio(portfolio: Portfolio) -> None:
    if len({a.asset_type for a in portfolio.assets}) != 1:
        raise ValueError("Портфель должен содержать одно семейство активов")
    for asset in portfolio.assets:
        model_for(asset.asset_type).validate(asset)


def validate_scenario(scenario: Scenario, portfolio: Portfolio, text: str) -> None:
    declarations = model_for(portfolio.assets[0].asset_type).drivers
    if len(scenario.shocks) != len(declarations) or {s.driver for s in scenario.shocks} != {
        d["name"] for d in declarations
    }:
        raise ValueError("Сценарий должен содержать каждый драйвер ровно один раз")
    for shock in scenario.shocks:
        field = next(d for d in declarations if d["name"] == shock.driver)
        if not field["min"] <= shock.low <= shock.high <= field["max"]:
            raise ValueError(f"{field['label']}: шок вне диапазона")
    if scenario.event_quote not in text:
        raise ValueError("Цитата должна дословно присутствовать в исходном событии")
