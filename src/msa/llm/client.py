"""Асинхронный клиент LLM с вызовами инструментов и ответом по заданной схеме."""

import httpx

from .providers import active
from .structured import decode, schema_instruction, with_schema


class LLMError(RuntimeError):
    pass


class LLMClient:
    mock = False

    def __init__(self, config):
        self.profile = active(config)
        if not self.profile.base_url:
            raise ValueError("Для LLM нужен endpoint")
        if self.profile.external and not self.profile.api_key:
            raise ValueError("Для DeepSeek задайте DEEPSEEK_API_KEY в окружении")
        self.description = self.profile.describe()

    async def chat(self, messages, *, tools=None, tool_choice=None, schema=None):
        p = self.profile
        payload = {"model": p.model, "messages": messages, "temperature": 0}
        if p.name == "deepseek":
            payload["thinking"] = {"type": "disabled"}
        if p.max_tokens:
            payload["max_tokens"] = p.max_tokens
        if tools:
            payload.update(tools=tools, tool_choice=tool_choice)
        if schema:
            payload["messages"] = [schema_instruction(schema), *messages]
            payload["response_format"] = (
                {"type": "json_object"} if p.json_mode == "json_object" else with_schema(schema)
            )
        async with httpx.AsyncClient(timeout=p.timeout) as client:
            try:
                response = await client.post(
                    p.base_url.rstrip("/") + "/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {p.api_key}"},
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                # Не возвращаем тело ответа, ключи или адреса с учётными данными.
                raise LLMError("LLM недоступна. Проверьте профиль и повторите этап.") from exc
        try:
            choice = response.json()["choices"][0]
            if choice["finish_reason"] == "length":
                raise LLMError("Ответ LLM обрезан. Увеличьте лимит токенов.")
            return choice["message"]
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError("LLM вернула некорректный ответ") from exc

    async def complete(self, messages, schema, *, tools=None):
        # После ответа MCP схемы tools сохраняют контекст диалога, но новый вызов
        # не нужен: этот запрос должен вернуть окончательный объект по schema.
        message = await self.chat(
            messages, schema=schema, tools=tools, tool_choice="none" if tools else None
        )
        try:
            return decode(message["content"], schema)
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMError("Ответ LLM не соответствует схеме. Повторите этап.") from exc
