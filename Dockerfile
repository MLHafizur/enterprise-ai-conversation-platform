FROM python:3.12.16-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

COPY pyproject.toml uv.lock README.md .python-version ./
COPY src ./src

RUN uv sync --frozen --no-dev --no-editable


FROM python:3.12.16-slim-bookworm AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system --gid 10001 chat \
    && useradd --system --uid 10001 --gid chat --home-dir /nonexistent chat

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --chown=chat:chat alembic.ini ./alembic.ini
COPY --chown=chat:chat migrations ./migrations

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "enterprise_chat_platform.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
