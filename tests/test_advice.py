import asyncio
import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from msa.agents import advise
from msa.config import ROOT
from msa.contracts import Advice, Portfolio, Scenario
from msa.db.migrate import migrate
from msa.db.store import Store
from msa.engine.pipeline import calculate
from msa.llm.mock import MockLLM
from msa.mcp import build_server
from msa.reporting import facts_for, validate_advice


def test_recommendations_reject_fabricated_numbers_and_references():
    advice = Advice(
        summary="Проверить допущения",
        recommendations=[
            {
                "action": "Проверить план",
                "rationale": "Есть риск",
                "evidence": ["unknown"],
            }
        ],
    )
    with pytest.raises(ValueError, match="отсутствующую"):
        validate_advice(advice, {})
    advice.recommendations[0].evidence = ["known"]
    advice.summary = "Потери 999 миллионов"
    with pytest.raises(ValueError, match="Числа"):
        validate_advice(advice, {"known": {}})


@pytest.mark.parametrize(
    "tool_name,arguments",
    [(None, "{}"), ("write_result", "{}"), ("get_calculation_results", '{"run_id":"other"}')],
)
def test_llm_cannot_skip_or_change_the_read_only_tool(case, tool_name, arguments):
    class InvalidLLM(MockLLM):
        async def chat(self, messages, **kwargs):
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": []
                if tool_name is None
                else [{"id": "invalid", "function": {"name": tool_name, "arguments": arguments}}],
            }

    # Хранилище недоступно: проверка должна отклонить запрос до вызова инструмента.
    with pytest.raises(ValueError):
        asyncio.run(advise(InvalidLLM(case), build_server(None, "current")))


def test_mcp_is_bound_to_run_and_rejects_cross_run_arguments(config, case):
    store = Store(config.database_url)
    migrate(store.engine)
    store.seed(ROOT / "demo/case.json")
    first = store.create({"text": "Первый запуск"}, MockLLM.description)
    second = store.create({"text": "Второй запуск"}, MockLLM.description)
    scenario = Scenario.model_validate(case["mock_scenario"])
    result = calculate(
        Portfolio.model_validate(case["portfolio"]), scenario, scenario.event_quote, 42, 128
    )
    store.save(first, result=result, scenario=scenario.model_dump())

    async def check():
        async with Client(build_server(store, first)) as client:
            response = await client.call_tool("get_calculation_results", {})
            assert response.structured_content["run_id"] == first
            with pytest.raises(ToolError):
                await client.call_tool("get_calculation_results", {"run_id": second})
        async with Client(build_server(store, second)) as client:
            with pytest.raises(ToolError):
                await client.call_tool("get_calculation_results", {})

    try:
        asyncio.run(check())
    finally:
        store.engine.dispose()


def test_real_http_client_tool_roundtrip(monkeypatch, config, case):
    import httpx

    from msa.llm.client import LLMClient

    store = Store(config.database_url)
    migrate(store.engine)
    store.seed(ROOT / "demo/case.json")
    run_id = store.create({"text": "Запуск"}, {"provider": "local"})
    scenario = Scenario.model_validate(case["mock_scenario"])
    result = calculate(
        Portfolio.model_validate(case["portfolio"]), scenario, scenario.event_quote, 42, 128
    )
    store.save(run_id, result=result, scenario=scenario.model_dump())
    requests = []
    original_client = httpx.AsyncClient

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            assert result["draws_sha256"] not in json.dumps(body["messages"])
            assert all(m["role"] != "tool" for m in body["messages"])
            assert body["tool_choice"]["function"]["name"] == "get_calculation_results"
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "get_calculation_results", "arguments": "{}"},
                    }
                ],
            }
        else:
            tool = next(m for m in body["messages"] if m["role"] == "tool")
            assert tool["tool_call_id"] == "call-1"
            data = json.loads(tool["content"])
            assert data["facts"] == facts_for(result)
            message = {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "summary": "Требуется проверка допущений.",
                        "recommendations": [
                            {
                                "action": "Запросить план компании",
                                "rationale": "Возможны нарушения ковенанта.",
                                "evidence": ["A1.breach_probability"],
                            }
                        ],
                    }
                ),
            }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": message,
                        "finish_reason": "tool_calls" if len(requests) == 1 else "stop",
                    }
                ]
            },
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    config.llm_provider = "local"
    try:
        advice, trace = asyncio.run(advise(LLMClient(config), build_server(store, run_id)))
        assert len(requests) == 2
        assert trace["requested_by"] == "llm"
        assert advice["recommendations"][0]["evidence"] == ["A1.breach_probability"]
    finally:
        store.engine.dispose()
