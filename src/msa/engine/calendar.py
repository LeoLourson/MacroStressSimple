"""Помесячный календарь: первый полный месяц после даты оценки и число дней в месяцах."""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date

import numpy as np

DEFAULT_HORIZON = 12


def add_months(day: date, months: int) -> date:
    offset = day.year * 12 + day.month - 1 + months
    year, month = divmod(offset, 12)
    return date(year, month + 1, min(day.day, monthrange(year, month + 1)[1]))


@dataclass(frozen=True)
class Calendar:
    """Сетка из horizon месяцев, отсчитываемых от даты оценки as_of."""

    as_of: date
    horizon: int = DEFAULT_HORIZON

    def __post_init__(self):
        if self.horizon < 1:
            raise ValueError("horizon must be at least one month")

    def _check(self, t: int) -> None:
        if not 1 <= t <= self.horizon:
            raise IndexError(f"month {t} is outside the horizon 1..{self.horizon}")

    def month_start(self, t: int) -> date:
        self._check(t)
        first_full = add_months(date(self.as_of.year, self.as_of.month, 1), 1)
        return add_months(first_full, t - 1)

    def month_end(self, t: int) -> date:
        start = self.month_start(t)
        return date(start.year, start.month, monthrange(start.year, start.month)[1])

    def days(self, t: int) -> int:
        start = self.month_start(t)
        return monthrange(start.year, start.month)[1]

    def day_counts(self) -> np.ndarray:
        return np.array([self.days(t) for t in self.months()], dtype=float)

    def months(self) -> range:
        return range(1, self.horizon + 1)

    def month_ends(self) -> list[date]:
        return [self.month_end(t) for t in self.months()]
