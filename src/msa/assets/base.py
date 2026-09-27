"""Расчётное ядро работает с описаниями и массивами, не зная имён финансовых входов."""

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from msa.contracts import Asset


@dataclass
class Evaluation:
    # Первая ось всех массивов — общие реализации; у monthly вторая ось — месяцы.
    # Агрегация обязана сохранять этот порядок до вычисления квантилей.
    annual: dict[str, np.ndarray]
    monthly: dict[str, np.ndarray]
    breach: np.ndarray


class AssetModel(Protocol):
    asset_type: str
    label: str
    fields: list[dict]
    drivers: list[dict]
    metrics: list[dict]
    assumptions: list[str]

    def validate(self, asset: Asset) -> None: ...

    def evaluate(
        self, asset: Asset, shocks: dict[str, np.ndarray], days: np.ndarray
    ) -> Evaluation: ...

    def aggregate(self, evaluations: list[Evaluation]) -> Evaluation: ...
