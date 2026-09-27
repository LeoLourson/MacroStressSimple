"""Модель кредита: постоянный долг, месячная операционная прибыль и покрытие процентов."""

import numpy as np

from msa.contracts import Asset

from .base import Evaluation


class CorporateLoan:
    asset_type = "corp_loan"
    label = "Корпоративный кредит"
    fields = [
        {"name": "revenue", "label": "Годовая выручка", "unit": "млн ₽", "min": 0},
        {"name": "cogs", "label": "Годовая себестоимость", "unit": "млн ₽", "min": 0},
        {"name": "opex", "label": "Годовые операционные расходы", "unit": "млн ₽", "min": 0},
        {"name": "debt", "label": "Остаток долга", "unit": "млн ₽", "min": 0.01},
        {"name": "rate", "label": "Годовая ставка", "unit": "доля", "min": 0.001},
        {"name": "icr_limit", "label": "Минимальное покрытие процентов", "unit": "×", "min": 0},
    ]
    drivers = [
        {
            "name": "revenue",
            "label": "Изменение выручки",
            "unit": "%",
            "scale": 100,
            "min": -0.9,
            "max": 1.0,
        },
        {
            "name": "cost",
            "label": "Изменение себестоимости",
            "unit": "%",
            "scale": 100,
            "min": -0.9,
            "max": 1.0,
        },
        {
            "name": "rate",
            "label": "Прибавка к ставке",
            "unit": "п.п.",
            "scale": 100,
            "min": 0.0,
            "max": 0.3,
        },
    ]
    metrics = [
        {
            "name": "ebitda",
            "label": "EBITDA",
            "unit": "млн ₽",
            "definition": "выручка − себестоимость − операционные расходы; сумма за весь горизонт",
        },
        {
            "name": "interest",
            "label": "Interest Expense",
            "unit": "млн ₽",
            "definition": "долг × годовая ставка × дни / 365; сумма за весь горизонт",
        },
        {
            "name": "icr",
            "label": "ICR",
            "unit": "×",
            "definition": "EBITDA за весь горизонт / проценты за весь горизонт; портфель — отношение сумм",
        },
    ]
    assumptions = [
        "Синтетические исходные данные.",
        "Годовая выручка и расходы распределены равномерно по двенадцати месяцам.",
        "Долг постоянен, ставка плавающая; проценты начисляются за фактическое число дней.",
        "Шоки постоянны на всём горизонте и одинаковы для всех компаний.",
        "Выручка и себестоимость изменяются независимо; операционные расходы постоянны.",
        "Ковенант нарушен, если месячный ICR хотя бы раз ниже указанного порога.",
        "Налоги, ликвидность, амортизация долга, PD и кредитные потери не моделируются.",
    ]

    def validate(self, asset: Asset) -> None:
        expected = {f["name"] for f in self.fields}
        if set(asset.inputs) != expected:
            raise ValueError(f"{asset.asset_id}: требуются ровно поля {sorted(expected)}")
        for field in self.fields:
            value = asset.inputs[field["name"]]
            if not np.isfinite(value) or value < field["min"]:
                raise ValueError(f"{asset.asset_id}: неверное поле {field['label']}")

    def evaluate(self, asset, shocks, days):
        self.validate(asset)
        p = asset.inputs
        count = len(shocks["revenue"])
        ebitda = (
            p["revenue"] * (1 + shocks["revenue"]) - p["cogs"] * (1 + shocks["cost"]) - p["opex"]
        ) / 12
        ebitda = np.broadcast_to(ebitda[:, None], (count, len(days)))
        # EBITDA равна по месяцам, проценты зависят от числа дней. Поэтому ICR
        # меняется по календарю даже при постоянных шоках; високосный день тоже учитывается.
        interest = p["debt"] * (p["rate"] + shocks["rate"][:, None]) * days / 365
        icr = ebitda / interest
        # Годовой ICR может быть выше порога при нарушении в отдельном месяце.
        return self._result(ebitda, interest, np.any(icr < p["icr_limit"], axis=1))

    def aggregate(self, evaluations):
        # Достаточное покрытие у одной компании не отменяет нарушение ковенанта
        # у другой; портфельное событие — нарушение хотя бы у одного заёмщика.
        return self._result(
            sum(e.monthly["ebitda"] for e in evaluations),
            sum(e.monthly["interest"] for e in evaluations),
            np.logical_or.reduce([e.breach for e in evaluations]),
        )

    @staticmethod
    def _result(ebitda, interest, breach):
        # ICR за горизонт — отношение сумм.
        annual_ebitda, annual_interest = ebitda.sum(axis=1), interest.sum(axis=1)
        return Evaluation(
            annual={
                "ebitda": annual_ebitda,
                "interest": annual_interest,
                "icr": annual_ebitda / annual_interest,
            },
            monthly={"ebitda": ebitda, "interest": interest, "icr": ebitda / interest},
            breach=breach,
        )
