import copy
import json

from fastapi.testclient import TestClient

from msa.api import create_app
from msa.contracts import Advice
from msa.llm.mock import MockLLM


class RecordingLLM(MockLLM):
    def __init__(self, case):
        super().__init__(case)
        self.messages = []
        self.fail_advice = False

    async def complete(self, messages, schema, *, tools=None):
        self.messages.append(copy.deepcopy(messages))
        if schema is Advice and self.fail_advice:
            raise ValueError("Simulated invalid answer")
        return await super().complete(messages, schema, tools=tools)


def create(client, case):
    response = client.post("/v1/runs", json={"text": case["events"][0]["text"], "points": 128})
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "awaiting_scenario", run
    assert "result" not in run
    return run


def confirm(client, run):
    return client.post(
        f"/v1/runs/{run['id']}/confirm",
        json={
            "scenario": run["scenario"],
            "actor": "Аналитик",
        },
    )


def test_full_run_restart_tool_call_and_approval(config, case):
    llm = RecordingLLM(case)
    with TestClient(create_app(config, llm)) as client:
        run = create(client, case)
        run_id = run["id"]
        assert client.get(f"/v1/runs/{run_id}/report").status_code == 409
        bad = copy.deepcopy(run["scenario"])
        bad["shocks"][0]["low"] = -2
        assert (
            client.post(
                f"/v1/runs/{run_id}/confirm",
                json={
                    "scenario": bad,
                    "actor": "Аналитик",
                },
            ).status_code
            == 422
        )
    # Снимок запуска и остановка LangGraph сохраняются после перезапуска приложения.
    with TestClient(create_app(config, llm)) as client:
        response = confirm(client, run)
        assert response.status_code == 200, response.text
        calculated = response.json()
        assert calculated["status"] == "awaiting_approval", calculated
        trace = calculated["tool_trace"]
        assert trace["tool"] == "get_calculation_results"
        assert trace["run_id"] == run_id
        assert trace["draws_sha256"] == calculated["result"]["draws_sha256"]
        dialogue = llm.messages[-1]
        assert any(m.get("tool_calls") for m in dialogue)
        tool_data = json.loads(next(m["content"] for m in dialogue if m["role"] == "tool"))
        assert tool_data["run_id"] == run_id
        assert tool_data["facts"]
        assert confirm(client, run).status_code == 409
        assert client.get(f"/v1/runs/{run_id}/report").status_code == 409
    with TestClient(create_app(config, llm)) as client:
        response = client.post(
            f"/v1/runs/{run_id}/decision", json={"action": "approve", "actor": "Преподаватель"}
        )
        assert response.json()["status"] == "approved"
        report = client.get(f"/v1/runs/{run_id}/report")
        assert report.status_code == 200
        assert "P5" in report.text and "P50" in report.text and "P95" in report.text
        assert "Преподаватель" in report.text
        assert (
            client.post(
                f"/v1/runs/{run_id}/decision", json={"action": "reject", "actor": "x"}
            ).status_code
            == 409
        )


def test_failed_advice_is_explicit_and_retry_keeps_calculation(config, case):
    llm = RecordingLLM(case)
    llm.fail_advice = True
    with TestClient(create_app(config, llm)) as client:
        run = create(client, case)
        failed = confirm(client, run).json()
        assert failed["status"] == "failed"
        assert "advice" not in failed
        previous = failed["result"]
        llm.fail_advice = False
        recovered = client.post(f"/v1/runs/{run['id']}/retry").json()
        assert recovered["status"] == "awaiting_approval", recovered
        assert recovered["result"] == previous
        rejected = client.post(
            f"/v1/runs/{run['id']}/decision", json={"action": "reject", "actor": "Преподаватель"}
        ).json()
        assert rejected["status"] == "rejected"
        assert client.get(f"/v1/runs/{run['id']}/report").status_code == 409


def test_validation_and_missing_run(config, case):
    with TestClient(create_app(config, MockLLM(case))) as client:
        assert client.post("/v1/runs", json={"text": "short"}).status_code == 422
        assert (
            client.post(
                "/v1/runs",
                json={
                    "text": case["events"][0]["text"],
                    "points": 129,
                },
            ).status_code
            == 422
        )
        assert client.get("/v1/runs/nonexistent").status_code == 404


def test_recover_before_first_checkpoint_and_at_pending_interrupt(config, case):
    application = create_app(config, MockLLM(case))
    with TestClient(application) as client:
        run_id = application.state.store.create(
            {"text": case["events"][0]["text"], "seed": 42, "points": 128},
            MockLLM.description,
        )
        recovered = client.post(f"/v1/runs/{run_id}/retry").json()
        assert recovered["status"] == "awaiting_scenario", recovered
        # Обрыв HTTP-запроса может оставить устаревший статус у сохранённой остановки.
        application.state.store.save(run_id, status="running")
        recovered = client.post(f"/v1/runs/{run_id}/retry").json()
        assert recovered["status"] == "awaiting_scenario", recovered
        assert confirm(client, recovered).json()["status"] == "awaiting_approval"
