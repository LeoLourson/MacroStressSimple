"""Общая рандомизированная матрица Соболя; независимые треугольные распределения шоков."""

import hashlib

import numpy as np
from scipy.stats import qmc, triang

from msa.contracts import Scenario


def draw(scenario: Scenario, seed: int, points: int) -> tuple[dict, str]:
    if points < 2 or points & (points - 1):
        raise ValueError("Sobol points must be a power of two")
    # Порядок полей в JSON не должен менять соответствие драйверов столбцам Соболя.
    shocks = sorted(scenario.shocks, key=lambda s: s.driver)
    uniforms = qmc.Sobol(d=len(shocks), scramble=True, seed=seed).random_base2(int(np.log2(points)))
    draws = {}
    for index, shock in enumerate(shocks):
        width = shock.high - shock.low
        draws[shock.driver] = (
            np.full(points, shock.mode)
            if width == 0
            else triang.ppf(
                uniforms[:, index], (shock.mode - shock.low) / width, loc=shock.low, scale=width
            )
        )
    # Фиксируем порядок байтов, чтобы хеш матрицы не зависел от архитектуры машины.
    matrix = np.column_stack(list(draws.values())).astype("<f8")
    return draws, hashlib.sha256(matrix.tobytes()).hexdigest()
