"""Общие настройки API, клиента LLM и локального запуска."""

import os
from pathlib import Path
from typing import Literal

from pydantic import Field, HttpUrl

from msa.contracts import Model

ROOT = Path(__file__).resolve().parents[2]


class Settings(Model):
    database_url: str = "postgresql://msa:local@127.0.0.1:5433/msa_product"
    checkpoint_path: str = ".data/test-checkpoints.sqlite"
    llm_provider: Literal["mock", "local", "deepseek"] = "mock"
    llm_base_url: HttpUrl | None = None
    llm_model: str | None = None
    llm_json_mode: Literal["json_schema", "json_object"] | None = None
    llm_timeout: float | None = Field(default=None, gt=0)
    llm_max_tokens: int | None = Field(default=None, gt=0)


def settings() -> Settings:
    return Settings.model_validate(
        {
            name: os.environ[f"MSA_{name.upper()}"]
            for name in Settings.model_fields
            if f"MSA_{name.upper()}" in os.environ
        }
    )
