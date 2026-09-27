"""Явные контракты обмена данными."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class Asset(Model):
    asset_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=40)
    name: str = Field(min_length=1, max_length=120)
    asset_type: str
    inputs: dict[str, float]
    source: str = Field(min_length=1)


class Portfolio(Model):
    name: str
    as_of: date
    assets: list[Asset] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({a.asset_id for a in self.assets}) != len(self.assets):
            raise ValueError("Идентификаторы активов должны быть уникальны")
        return self


class Shock(Model):
    driver: str
    low: float
    mode: float
    high: float
    explanation: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def ordered(self):
        if not self.low <= self.mode <= self.high:
            raise ValueError("Нужно low ≤ mode ≤ high")
        return self


class Scenario(Model):
    title: str = Field(min_length=1, max_length=160)
    transmission: list[str] = Field(min_length=2, max_length=5)
    event_quote: str = Field(min_length=1, max_length=1000)
    shocks: list[Shock] = Field(min_length=1, max_length=8)


class RunRequest(Model):
    text: str = Field(min_length=10, max_length=12000)
    seed: int = Field(default=42, ge=0, le=2**32 - 1)
    points: int = Field(default=1024, ge=128, le=8192)

    @model_validator(mode="after")
    def power_of_two(self):
        if self.points & (self.points - 1):
            raise ValueError("Число реализаций должно быть степенью двойки")
        return self


class Confirmation(Model):
    scenario: Scenario
    actor: str = Field(min_length=1, max_length=80)


class Decision(Model):
    action: Literal["approve", "reject"]
    actor: str = Field(min_length=1, max_length=80)


class Recommendation(Model):
    action: str = Field(min_length=1, max_length=500)
    rationale: str = Field(min_length=1, max_length=1200)
    evidence: list[str] = Field(min_length=1, max_length=8)


class Advice(Model):
    summary: str = Field(min_length=1, max_length=1500)
    recommendations: list[Recommendation] = Field(min_length=1, max_length=5)
