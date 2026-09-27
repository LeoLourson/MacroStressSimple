"""Именованные профили языковой модели.

- ``local`` — LM Studio или другой локальный сервер с JSON и вызовами инструментов.
- ``deepseek`` — внешний API DeepSeek для синтетического кейса.
- ``mock`` — воспроизводимая заглушка без сетевых запросов.

Профиль выбирается через ``MSA_LLM_PROVIDER``, параметры — через ``MSA_LLM_*``.
"""

import os
from dataclasses import dataclass

from msa.config import settings

from .network import on_prem_url


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str | None
    model: str
    api_key: str
    json_mode: str
    timeout: float
    max_tokens: int | None
    external: bool

    def describe(self) -> dict:
        """Публичное описание профиля для интерфейса, без ключа доступа."""
        return {
            "provider": self.name,
            "model": self.model,
            "base_url": self.base_url,
            "external": self.external,
        }


_PROFILES = {
    "local": {
        "base_url": "http://127.0.0.1:1234/v1",  # Стандартный адрес сервера LM Studio.
        "model": "msa-local",
        "keys": ("MSA_LLM_API_KEY", "VLLM_API_KEY"),
        "json_mode": "json_schema",
        # Локальной модели может потребоваться больше времени на ответ.
        "timeout": 300.0,
        "max_tokens": None,
        "external": False,
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-flash",
        "keys": ("DEEPSEEK_API_KEY",),
        # В режиме JSON DeepSeek схема передаётся в инструкции, без строгого json_schema.
        "json_mode": "json_object",
        "timeout": 180.0,
        "max_tokens": 8192,
        "external": True,
    },
    "mock": {
        "base_url": None,
        "model": "mock",
        "keys": (),
        "json_mode": "json_schema",
        "timeout": 1.0,
        "max_tokens": None,
        "external": False,
    },
}


def active(config=None) -> Provider:
    config = config or settings()
    name = config.llm_provider
    profile = _PROFILES[name]
    base_url = str(config.llm_base_url) if config.llm_base_url else profile["base_url"]
    if name == "local":
        # Внешний адрес в локальном профиле считается ошибкой конфигурации.
        base_url = on_prem_url(base_url)
    model = profile["model"]
    if name == "local":
        # Сервер vLLM может задавать имя модели через VLLM_SERVED_MODEL_NAME.
        model = os.getenv("VLLM_SERVED_MODEL_NAME", model)
    model = config.llm_model or model
    key = next((os.environ[k] for k in profile["keys"] if os.getenv(k)), "")
    return Provider(
        name=name,
        base_url=base_url,
        model=model,
        api_key=key,
        json_mode=config.llm_json_mode or profile["json_mode"],
        timeout=config.llm_timeout or profile["timeout"],
        max_tokens=config.llm_max_tokens or profile["max_tokens"],
        external=profile["external"],
    )
