FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# The project is not installed as a distribution (--no-install-project):
# without .git in the image hatch-vcs cannot derive a version, and the
# code is copied anyway and imported from WORKDIR.
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen --no-install-project

FROM python:3.13-slim AS runtime

WORKDIR /app

COPY --from=builder /app/.venv .venv
COPY . .

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8014

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8014"]
