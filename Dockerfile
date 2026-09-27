# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY src/ src/
COPY migrations/ migrations/
COPY demo/ demo/
COPY ui/ ui/
COPY scripts/rehearse.py scripts/rehearse.py
# Keep the source layout: demo data and migrations are resolved relative to it.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8091 8501
CMD ["uvicorn", "msa.api:app", "--host", "0.0.0.0", "--port", "8091", "--workers", "1"]
