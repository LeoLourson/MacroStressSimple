"""Автономная заглушка; инструменты вызываются через настоящий клиент FastMCP."""

import json

from msa.contracts import Advice, Scenario


class MockLLM:
    mock = True
    description = {"provider": "mock", "model": "заглушка", "external": False}

    def __init__(self, case):
        self.case = case

    async def chat(self, messages, *, tools=None, tool_choice=None, schema=None):
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "result-call",
                    "type": "function",
                    "function": {"name": "get_calculation_results", "arguments": "{}"},
                }
            ],
        }

    async def complete(self, messages, schema, *, tools=None):
        if schema is Scenario:
            request = json.loads(messages[-1]["content"])
            scenario = {**self.case["mock_scenario"], "event_quote": request["text"][:200]}
            return Scenario.model_validate(scenario)
        payload = json.loads(next(m["content"] for m in reversed(messages) if m["role"] == "tool"))
        facts = payload["facts"]
        risk = max(
            (
                key
                for key in facts
                if key.endswith(".breach_probability") and not key.startswith("portfolio.")
            ),
            key=lambda key: facts[key]["value"],
        )
        return Advice(
            summary="Пример рекомендации по результатам расчёта. "
            "Формулировка создана заглушкой, а не языковой моделью.",
            recommendations=[
                {
                    "action": "Проверить компанию с наибольшей вероятностью нарушения.",
                    "rationale": "Сопоставить финансовый план и допущения сценария; "
                    "обсудить меры при ухудшении покрытия процентов.",
                    "evidence": [risk],
                }
            ],
        )
