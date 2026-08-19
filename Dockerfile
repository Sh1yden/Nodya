FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

FROM python:3.13-slim AS runtime

WORKDIR /app

COPY --from=builder /app/.venv .venv
COPY . .

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8014

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8014"]
