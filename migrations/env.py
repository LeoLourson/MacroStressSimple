from alembic import context

from msa.config import settings
from msa.db.store import Store, metadata

config = context.config


def run(connection):
    context.configure(
        connection=connection, target_metadata=metadata, version_table="alembic_version"
    )
    with context.begin_transaction():
        context.run_migrations()


supplied = config.attributes.get("connection")
if supplied is not None:
    run(supplied)
else:
    store = Store(settings().database_url)
    try:
        with store.engine.begin() as connection:
            run(connection)
    finally:
        store.engine.dispose()
