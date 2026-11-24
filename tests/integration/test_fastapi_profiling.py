import asyncio
import os

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.core.config import Settings
from app.core.database import create_engine
from app.core.profiling import current_profile, profile_request
from app.main import create_app

pytestmark = pytest.mark.asyncio


@pytest.fixture
def profiled_settings() -> Settings:
    dsn = os.getenv("TEST_DATABASE_URL")
    if dsn is None:
        pytest.skip("Set TEST_DATABASE_URL to a migrated PostgreSQL test database")
    url = make_url(dsn)
    assert url.drivername == "postgresql+asyncpg"
    return Settings(
        postgres_host=url.host or "localhost",
        postgres_port=url.port or 5432,
        postgres_db=url.database or "performance_lab",
        postgres_user=url.username or "lab",
        postgres_password=url.password or "",
        api_profiling_enabled=True,
        analytics_cache_enabled=False,
    )


async def test_profile_headers_are_request_local_under_concurrency(
    profiled_settings: Settings,
) -> None:
    app = create_app(profiled_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://localhost"
        ) as client:
            path = "/api/v1/optimized/analytics/customers"
            responses = await asyncio.gather(*(client.get(path) for _ in range(4)))
            metrics = await client.get("/metrics")
    for response in responses:
        assert response.status_code == 200
        assert response.headers["X-SQL-Queries"] == "1"
        phases = dict(
            section.strip().split(";dur=", maxsplit=1)
            for section in response.headers["Server-Timing"].split(",")
        )
        assert float(phases["server"]) >= float(phases["sql"]) > 0
        assert float(phases["pool"]) > 0
        assert float(phases["serialization"]) > 0
        assert float(phases["cache"]) == 0
        assert float(phases["logic"]) > 0
    assert "Server-Timing" not in metrics.headers
    assert current_profile.get() is None


async def test_disabled_profiling_has_no_headers(profiled_settings: Settings) -> None:
    app = create_app(
        profiled_settings.model_copy(update={"api_profiling_enabled": False})
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://localhost"
        ) as client:
            response = await client.get("/api/v1/optimized/users/search?limit=1")
    assert response.status_code == 200
    assert "Server-Timing" not in response.headers
    assert "X-SQL-Queries" not in response.headers


async def test_failed_sql_counts_toward_request_time(
    profiled_settings: Settings,
) -> None:
    engine = create_engine(profiled_settings)
    try:
        with profile_request() as profile, pytest.raises(DBAPIError):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1 / 0"))
        assert profile.sql_count == 1
        assert profile.sql_ns > 0
        assert profile.pool_ns > 0
    finally:
        await engine.dispose()
