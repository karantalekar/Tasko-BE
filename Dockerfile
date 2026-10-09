FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM build AS test
RUN uv sync --frozen --no-install-project
COPY app ./app
COPY tests ./tests
CMD ["uv", "run", "--frozen", "pytest", "-q"]

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app
RUN groupadd --gid 10001 tasko && useradd --uid 10001 --gid tasko --no-create-home tasko
COPY --from=build /app/.venv /app/.venv
COPY app ./app
USER tasko
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers"]
