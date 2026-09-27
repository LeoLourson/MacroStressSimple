"""Инструменты FastMCP привязаны к одному запуску и не изменяют данные."""

from fastmcp import Client, FastMCP

from msa.contracts import Portfolio, Scenario
from msa.engine.pipeline import calculate
from msa.reporting import facts_for


def build_server(store, run_id):
    # run_id захвачен замыканием, а не передан в схеме инструмента: модель не может
    # выбрать другой запуск. Это ограничение области инструментов, не авторизация API.
    server = FastMCP("macrostress")

    @server.tool(annotations={"readOnlyHint": True})
    def get_portfolio() -> dict:
        """Прочитать зафиксированный снимок портфеля текущего запуска."""
        return store.get(run_id)["portfolio"]

    @server.tool(annotations={"readOnlyHint": True})
    def run_calculation() -> dict:
        """Рассчитать подтверждённый сценарий, не изменяя сохранённые данные."""
        run = store.get(run_id)
        if not run.get("confirmation"):
            raise ValueError("Сценарий ещё не подтверждён")
        return calculate(
            Portfolio.model_validate(run["portfolio"]),
            Scenario.model_validate(run["scenario"]),
            run["request"]["text"],
            run["request"]["seed"],
            run["request"]["points"],
        )

    @server.tool(annotations={"readOnlyHint": True})
    def get_calculation_results() -> dict:
        """Прочитать результаты, допущения и факты для ссылок только текущего запуска."""
        run = store.get(run_id)
        if not run.get("result"):
            raise ValueError("Расчёт ещё не выполнен")
        result = run["result"]
        return {
            "run_id": run_id,
            "scenario": run["scenario"],
            "assumptions": result["assumptions"],
            "facts": facts_for(result),
            "model_version": result["model_version"],
            "draws_sha256": result["draws_sha256"],
        }

    return server


async def call(server, name):
    async with Client(server) as client:
        response = await client.call_tool(name, {})
        return response.structured_content
