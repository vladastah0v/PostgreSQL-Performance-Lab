FROM python:3.12-slim AS dependencies

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /bin/uv
WORKDIR /lab
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev

FROM python:3.12-slim

ENV PATH="/lab/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /lab
RUN groupadd --gid 10001 lab \
    && useradd --uid 10001 --gid lab --no-create-home --shell /usr/sbin/nologin lab
COPY --from=dependencies /lab/.venv /lab/.venv
COPY app ./app
COPY migrations ./migrations
COPY scripts ./scripts
COPY alembic.ini ./
COPY infra/api/entrypoint.sh /usr/local/bin/lab-entrypoint

USER lab
EXPOSE 8000
ENTRYPOINT ["/bin/sh", "/usr/local/bin/lab-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--log-config", "app/core/logging.json"]
