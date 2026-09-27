"""Отдельная история Alembic; существующие таблицы приложения не удаляются."""

from pathlib import Path

from alembic import command
from alembic.config import Config


def migrate(bind):
    root = Path(__file__).resolve().parents[3]
    config = Config(str(root / "migrations/alembic.ini"))
    with bind.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
