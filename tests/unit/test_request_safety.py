import json
import logging
from unittest.mock import AsyncMock

import asyncpg
import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.logging import JsonFormatter
from app.main import create_app
from app.schemas.parameters import UserSearch


def test_access_log_omits_query_parameters():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "server.py",
        1,
        '%s - "%s %s HTTP/%s" %d',
        (
            "127.0.0.1:12345",
            "GET",
            "/api/v1/optimized/users/search?email=a@example.test",
            "1.1",
            200,
        ),
        None,
    )
    entry = json.loads(JsonFormatter().format(record))
    assert entry["path"] == "/api/v1/optimized/users/search"
    assert entry["method"] == "GET" and entry["status"] == 200
    assert "a@example.test" not in str(entry)


def test_text_filter_rejects_null_characters_before_database_access():
    with pytest.raises(ValidationError, match="cannot contain null characters"):
        UserSearch(name="sample\x00text")


@pytest.mark.asyncio
async def test_database_connection_failure_returns_service_unavailable(monkeypatch):
    connect = AsyncMock(side_effect=ConnectionRefusedError("database offline"))
    monkeypatch.setattr(asyncpg, "connect", connect)
    app = create_app(Settings(db_connect_timeout=0.4, analytics_cache_enabled=False))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as client:
            response = await client.get("/api/v1/optimized/users/search?limit=1")
    assert response.status_code == 503
    assert response.json() == {"detail": "Database is unavailable"}
    connect.assert_awaited_once()
    assert connect.await_args.kwargs["timeout"] == 0.4
