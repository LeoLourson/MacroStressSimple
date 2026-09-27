"""Сквозная проверка локального API с двумя тестовыми подтверждениями."""

import httpx

API = "http://127.0.0.1:8091"


def main():
    with httpx.Client(base_url=API, timeout=900) as client:
        demo = client.get("/v1/demo").raise_for_status().json()
        run = (
            client.post("/v1/runs", json={"text": demo["events"][0]["text"]})
            .raise_for_status()
            .json()
        )
        assert run["status"] == "awaiting_scenario", run.get("error")
        run = (
            client.post(
                f"/v1/runs/{run['id']}/confirm",
                json={
                    "scenario": run["scenario"],
                    "actor": "Репетиция",
                },
            )
            .raise_for_status()
            .json()
        )
        assert run["status"] == "awaiting_approval", run.get("error")
        assert run["tool_trace"]["tool"] == "get_calculation_results"
        run = (
            client.post(
                f"/v1/runs/{run['id']}/decision",
                json={
                    "action": "approve",
                    "actor": "Репетиция",
                },
            )
            .raise_for_status()
            .json()
        )
        assert run["status"] == "approved"
        report = client.get(f"/v1/runs/{run['id']}/report").raise_for_status()
        assert "P5" in report.text and "P50" in report.text and "P95" in report.text
        print(f"OK: {run['id']} — сценарий, расчёт, MCP, рекомендации, утверждённый отчёт")


if __name__ == "__main__":
    main()
