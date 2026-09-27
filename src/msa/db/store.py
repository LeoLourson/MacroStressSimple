import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import JSON, Column, MetaData, String, Table, create_engine, insert, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.pool import StaticPool

from msa.assets import validate_portfolio
from msa.contracts import Portfolio

metadata = MetaData()
portfolios = Table(
    "portfolio",
    metadata,
    Column("id", String, primary_key=True),
    Column("data", JSON, nullable=False),
)
runs = Table(
    "run",
    metadata,
    Column("id", String, primary_key=True),
    Column("created_at", String, nullable=False),
    Column("data", JSON, nullable=False),
)


def now():
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, url):
        parsed = make_url(url)
        if parsed.drivername in {"postgresql", "postgres"}:
            parsed = parsed.set(drivername="postgresql+psycopg")
        options = {}
        if parsed.get_backend_name() == "sqlite":
            options["connect_args"] = {"check_same_thread": False}
            if parsed.database in {"", ":memory:", None}:
                # База в памяти принадлежит соединению; отдельные соединения дали бы
                # обработчикам API разные базы без общих таблиц и запусков.
                options["poolclass"] = StaticPool
        self.engine = create_engine(parsed, **options)

    def seed(self, path: Path):
        case = json.loads(path.read_text(encoding="utf-8"))
        portfolio = Portfolio.model_validate(case["portfolio"])
        validate_portfolio(portfolio)
        with self.engine.begin() as connection:
            if connection.execute(select(portfolios.c.id)).first() is None:
                connection.execute(
                    insert(portfolios).values(
                        id="default", data=portfolio.model_dump(mode="json")
                    )
                )
        return case

    def portfolio(self):
        with self.engine.connect() as connection:
            return connection.execute(select(portfolios.c.data)).scalar_one()

    def create(self, request, provider):
        run_id = str(uuid4())
        # Снимок хранится внутри запуска: смена исходного портфеля не должна менять
        # входные данные уже начатого расчёта или его повтора.
        data = {
            "status": "running",
            "request": request,
            "provider": provider,
            "portfolio": self.portfolio(),
            "error": None,
        }
        with self.engine.begin() as connection:
            connection.execute(insert(runs).values(id=run_id, created_at=now(), data=data))
        return run_id

    def get(self, run_id):
        with self.engine.connect() as connection:
            row = connection.execute(select(runs).where(runs.c.id == run_id)).mappings().first()
        if row is None:
            raise KeyError(run_id)
        return {**row["data"], "id": row["id"], "created_at": row["created_at"]}

    def save(self, run_id, **changes):
        # Чтение и замена JSON не составляют атомарное слияние. Параллельные записи
        # сериализует mutation() в API; несколько процессов этим не защищены.
        current = self.get(run_id)
        current.pop("id")
        current.pop("created_at")
        with self.engine.begin() as connection:
            connection.execute(
                update(runs).where(runs.c.id == run_id).values(data={**current, **changes})
            )

    def list(self):
        with self.engine.connect() as connection:
            rows = connection.execute(select(runs).order_by(runs.c.created_at.desc()).limit(50))
            return [
                {
                    "id": row.id,
                    "created_at": row.created_at,
                    "status": row.data["status"],
                    "title": row.data.get("scenario", {}).get("title", "Новый стресс-тест"),
                }
                for row in rows
            ]
