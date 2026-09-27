"""Схема независима от прежних рабочего и демонстрационного контуров."""

import sqlalchemy as sa
from alembic import op

revision = "prod_0002"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "portfolio",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("data", sa.JSON, nullable=False),
    )
    op.create_table(
        "run",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("created_at", sa.String, nullable=False),
        sa.Column("data", sa.JSON, nullable=False),
    )


def downgrade():
    op.drop_table("run")
    op.drop_table("portfolio")
