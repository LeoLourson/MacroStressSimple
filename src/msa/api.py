import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from psycopg import AsyncConnection
from psycopg.conninfo import make_conninfo
from sqlalchemy.engine import make_url

from msa.assets import catalog, validate_scenario
from msa.config import ROOT, settings
from msa.contracts import Confirmation, Decision, Portfolio, RunRequest
from msa.db.migrate import migrate
from msa.db.store import Store
from msa.llm.client import LLMClient
from msa.llm.mock import MockLLM
from msa.reporting import facts_for, markdown
from msa.workflow import build_graph


@asynccontextmanager
async def checkpoints(config):
    url = make_url(config.database_url)
    if url.get_backend_name() == "sqlite":
        # SQLite используется в тестах; локальный стенд всегда запускает PostgreSQL.
        Path(config.checkpoint_path).parent.mkdir(parents=True, exist_ok=True)
        async with AsyncSqliteSaver.from_conn_string(config.checkpoint_path) as saver:
            yield saver
    else:
        conninfo = url.set(drivername="postgresql").render_as_string(hide_password=False)
        async with await AsyncConnection.connect(conninfo, autocommit=True) as connection:
            await connection.execute("CREATE SCHEMA IF NOT EXISTS checkpoints")
        conninfo = make_conninfo(conninfo, options="-c search_path=checkpoints")
        async with AsyncPostgresSaver.from_conn_string(conninfo) as saver:
            await saver.setup()
            yield saver


def create_app(config=None, llm=None):
    config = config or settings()

    @asynccontextmanager
    async def lifespan(app):
        store = Store(config.database_url)
        try:
            migrate(store.engine)
            case = store.seed(ROOT / "demo/case.json")
            language_model = llm or (
                MockLLM(case) if config.llm_provider == "mock" else LLMClient(config)
            )
            async with checkpoints(config) as saver:
                app.state.store = store
                app.state.case = case
                app.state.llm = language_model
                app.state.graph = build_graph(store, language_model, saver)
                app.state.busy = asyncio.Lock()
                yield
        finally:
            store.engine.dispose()

    app = FastAPI(title="MacroStress · кейс", lifespan=lifespan)

    def get(run_id):
        try:
            run = app.state.store.get(run_id)
            if run.get("result"):
                run["facts"] = facts_for(run["result"])
            return run
        except KeyError:
            raise HTTPException(404, "Запуск не найден") from None

    async def drive(run_id, value):
        graph = app.state.graph
        store = app.state.store
        store.save(run_id, status="running", error=None)
        try:
            run_config = {"configurable": {"thread_id": run_id}}
            if value is None:
                snapshot = await graph.aget_state(run_config)
                # None продолжает сохранённый граф; начальное состояние нужно только
                # при сбое до первого checkpoint, иначе можно повторить весь сценарий.
                if not snapshot.values:
                    value = {"run_id": run_id}
            await graph.ainvoke(value, run_config)
            snapshot = await graph.aget_state(run_config)
            if snapshot.interrupts:
                kind = snapshot.interrupts[0].value["kind"]
                store.save(
                    run_id,
                    status="awaiting_scenario" if kind == "scenario" else "awaiting_approval",
                )
        except Exception as exc:
            # Сохраняем ошибку для повтора этапа, без подмены модели заглушкой и выдуманных советов.
            logging.getLogger(__name__).error("Run %s failed: %s", run_id, type(exc).__name__)
            store.save(
                run_id,
                status="failed",
                error="Этап не завершён. Проверьте LLM и входные данные, затем повторите.",
            )
        return get(run_id)

    @asynccontextmanager
    async def mutation():
        # Один локальный пользователь и один процесс API; очереди и фонового исполнителя нет.
        if app.state.busy.locked():
            raise HTTPException(409, "Выполняется другой этап. Дождитесь завершения.")
        async with app.state.busy:
            yield

    @app.get("/health")
    def health():
        return {"status": "ok", "application": "macrostress"}

    @app.get("/v1/asset-types")
    def asset_types():
        return catalog()

    @app.get("/v1/demo")
    def demo():
        return {
            "portfolio": app.state.store.portfolio(),
            "events": app.state.case["events"],
            "provider": app.state.llm.description,
        }

    @app.get("/v1/runs")
    def list_runs():
        return app.state.store.list()

    @app.post("/v1/runs", status_code=201)
    async def new_run(request: RunRequest):
        async with mutation():
            run_id = app.state.store.create(request.model_dump(), app.state.llm.description)
            return await drive(run_id, {"run_id": run_id})

    @app.get("/v1/runs/{run_id}")
    def read_run(run_id: str):
        return get(run_id)

    @app.post("/v1/runs/{run_id}/confirm")
    async def confirm(run_id: str, confirmation: Confirmation):
        async with mutation():
            run = get(run_id)
            if run["status"] != "awaiting_scenario":
                raise HTTPException(409, "Этот запуск не ожидает подтверждения сценария")
            try:
                validate_scenario(
                    confirmation.scenario,
                    Portfolio.model_validate(run["portfolio"]),
                    run["request"]["text"],
                )
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            return await drive(run_id, Command(resume=confirmation.model_dump(mode="json")))

    @app.post("/v1/runs/{run_id}/decision")
    async def decide(run_id: str, decision: Decision):
        async with mutation():
            if get(run_id)["status"] != "awaiting_approval":
                raise HTTPException(409, "Этот запуск не ожидает решения по отчёту")
            return await drive(run_id, Command(resume=decision.model_dump()))

    @app.post("/v1/runs/{run_id}/retry")
    async def retry(run_id: str):
        async with mutation():
            run = get(run_id)
            if run["status"] not in {"failed", "running", "calculating", "recommending"}:
                raise HTTPException(409, "Нет незавершённого этапа")
            # Продолжаем с checkpoint. Запись Store и checkpoint не атомарны:
            # узел, успевший сохранить результат перед сбоем, может выполниться снова.
            return await drive(run_id, None)

    @app.get("/v1/runs/{run_id}/report", response_class=PlainTextResponse)
    def report(run_id: str):
        run = get(run_id)
        if run["status"] != "approved":
            raise HTTPException(409, "Скачивание отчёта доступно после утверждения")
        return PlainTextResponse(markdown(run), media_type="text/markdown")

    return app


app = create_app()
