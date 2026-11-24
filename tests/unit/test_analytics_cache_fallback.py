from argparse import Namespace
from unittest.mock import AsyncMock

import pytest
from prometheus_client import CollectorRegistry
from pydantic import TypeAdapter
from redis.exceptions import ConnectionError

from app.core.config import Settings
from app.schemas.parameters import DateRange
from app.services.analytics_cache import AnalyticsCache, cache_counter
from scripts import generate_data


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_unavailable_redis_and_disabled_cache_bypass_to_source(
    enabled: bool,
) -> None:
    redis = AsyncMock()
    redis.get.side_effect = ConnectionError("Redis unavailable")
    registry = CollectorRegistry()
    cache = AnalyticsCache(
        Settings(analytics_cache_enabled=enabled), redis, cache_counter(registry)
    )
    load = AsyncMock(return_value=17)

    assert await cache.get_or_load(
        "customers", DateRange(), TypeAdapter(int), load
    ) == (17, "BYPASS")
    load.assert_awaited_once()
    assert redis.get.await_count == int(enabled)
    assert (
        registry.get_sample_value(
            "analytics_cache_requests_total",
            {"operation": "customers", "outcome": "error" if enabled else "bypass"},
        )
        == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["generated", "cleared", "reused"])
async def test_generator_invalidates_cache_only_after_a_committed_change(
    monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    settings = Settings(postgres_host="127.0.0.1", postgres_db="performance_lab")
    monkeypatch.setattr(generate_data, "Settings", lambda: settings)
    connection = AsyncMock()
    monkeypatch.setattr(
        generate_data.asyncpg, "connect", AsyncMock(return_value=connection)
    )
    mutate = AsyncMock(return_value={"action": action})
    monkeypatch.setattr(generate_data, "generate_dataset", mutate)
    monkeypatch.setattr(generate_data, "clear_dataset", mutate)
    redis = AsyncMock()
    redis.__aenter__.return_value = redis
    monkeypatch.setattr(generate_data, "create_redis", lambda settings: redis)

    async def invalidate(redis_client, database_settings):
        mutate.assert_awaited_once()
        connection.close.assert_awaited_once()
        assert redis_client is redis
        assert database_settings is settings

    invalidate_cache = AsyncMock(side_effect=invalidate)
    monkeypatch.setattr(generate_data, "invalidate_analytics_cache", invalidate_cache)
    args = Namespace(
        profile="small",
        users=None,
        orders=None,
        events=None,
        seed=42,
        batch_size=100,
        clear=action == "cleared",
        confirm_clear=settings.postgres_db,
        strategy=generate_data.LoadStrategy.COPY,
    )

    assert (await generate_data.run(args))["action"] == action
    assert invalidate_cache.await_count == int(action != "reused")
