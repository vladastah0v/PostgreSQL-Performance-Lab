import asyncio
import os
from collections.abc import AsyncIterator
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from prometheus_client import CollectorRegistry
from pydantic import TypeAdapter
from redis.asyncio import Redis
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.optimized.routes import get_analytics_cache
from app.core.config import Settings
from app.core.database import get_db_session
from app.core.redis import invalidate_analytics_cache
from app.main import create_app
from app.schemas.parameters import DateRange
from app.schemas.responses import CustomerStats
from app.services.analytics_cache import AnalyticsCache, CacheBusy, cache_counter

pytestmark = pytest.mark.asyncio
QUERY = DateRange()
ADAPTER = TypeAdapter(CustomerStats)


def stats(revenue: str) -> CustomerStats:
    return CustomerStats(
        total_customers=10,
        customers_with_orders=2,
        repeat_customers=1,
        order_count=3,
        revenue=Decimal(revenue),
    )


@pytest_asyncio.fixture
async def redis_client() -> AsyncIterator[Redis]:
    url = os.getenv("TEST_REDIS_URL")
    if url is None:
        pytest.skip("Set TEST_REDIS_URL to run Redis integration tests")
    async with Redis.from_url(url, decode_responses=True) as client:
        await client.ping()
        yield client


@pytest_asyncio.fixture
async def settings(redis_client: Redis) -> AsyncIterator[Settings]:
    settings = Settings(
        analytics_cache_enabled=True, analytics_cache_namespace=f"test-{uuid4().hex}"
    )
    prefix = AnalyticsCache(
        settings, redis_client, cache_counter(CollectorRegistry())
    ).prefix
    try:
        yield settings
    finally:
        keys = [key async for key in redis_client.scan_iter(match=f"{prefix}:*")]
        if keys:
            await redis_client.delete(*keys)


@pytest_asyncio.fixture
async def cache(settings: Settings, redis_client: Redis) -> AnalyticsCache:
    return AnalyticsCache(settings, redis_client, cache_counter(CollectorRegistry()))


async def test_parameter_keys_and_invalidation(
    cache: AnalyticsCache, settings: Settings, redis_client: Redis
) -> None:
    load = AsyncMock(side_effect=[stats("1.25"), stats("2.50"), stats("3.75")])
    filtered = DateRange(date_from="2025-01-01")
    assert await cache.get_or_load("customers", QUERY, ADAPTER, load) == (
        stats("1.25"),
        "MISS",
    )
    assert await cache.get_or_load("customers", filtered, ADAPTER, load) == (
        stats("2.50"),
        "MISS",
    )
    assert await cache.get_or_load("customers", QUERY, ADAPTER, load) == (
        stats("1.25"),
        "HIT",
    )

    await invalidate_analytics_cache(redis_client, settings)
    assert await cache.get_or_load("customers", QUERY, ADAPTER, load) == (
        stats("3.75"),
        "MISS",
    )
    assert load.await_count == 3


async def test_concurrent_misses_use_one_loader(
    cache: AnalyticsCache, settings: Settings, redis_client: Redis
) -> None:
    second = AnalyticsCache(settings, redis_client, cache_counter(CollectorRegistry()))
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def load() -> CustomerStats:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return stats("1.25")

    tasks = [
        asyncio.create_task(instance.get_or_load("customers", QUERY, ADAPTER, load))
        for instance in (cache, second) * 4
    ]
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        await asyncio.sleep(0.1)
    finally:
        release.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=3)
    assert calls == 1
    assert [status for _, status in results].count("MISS") == 1
    assert all(result == stats("1.25") for result, _ in results)


async def test_invalidation_during_load_fences_out_old_result(
    cache: AnalyticsCache, settings: Settings, redis_client: Redis
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def old_source() -> CustomerStats:
        started.set()
        await release.wait()
        return stats("1.25")

    request = asyncio.create_task(
        cache.get_or_load("customers", QUERY, ADAPTER, old_source)
    )
    current_source = AsyncMock(return_value=stats("9.75"))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        await invalidate_analytics_cache(redis_client, settings)
        assert await cache.get_or_load("customers", QUERY, ADAPTER, current_source) == (
            stats("9.75"),
            "MISS",
        )
    finally:
        release.set()
        await asyncio.wait_for(request, timeout=2)
    assert await cache.get_or_load("customers", QUERY, ADAPTER, current_source) == (
        stats("9.75"),
        "HIT",
    )
    current_source.assert_awaited_once()


async def test_expiry_is_counted_from_load_start(
    settings: Settings, redis_client: Redis
) -> None:
    cache = AnalyticsCache(
        settings.model_copy(update={"analytics_cache_ttl": 1}),
        redis_client,
        cache_counter(CollectorRegistry()),
    )

    async def load() -> CustomerStats:
        await asyncio.sleep(0.2)
        return stats("1.25")

    assert (await cache.get_or_load("customers", QUERY, ADAPTER, load))[1] == "MISS"
    [key] = [
        key
        async for key in redis_client.scan_iter(match=f"{cache.prefix}:*:customers:*")
    ]
    assert 0 < await redis_client.pttl(key) <= 900
    async with asyncio.timeout(2):
        while await redis_client.exists(key):
            await asyncio.sleep(0.025)
    assert (await cache.get_or_load("customers", QUERY, ADAPTER, load))[1] == "MISS"


async def test_version_eviction_cannot_revive_an_old_value(
    cache: AnalyticsCache, redis_client: Redis
) -> None:
    load = AsyncMock(side_effect=[stats("1.25"), stats("9.75")])
    assert (await cache.get_or_load("customers", QUERY, ADAPTER, load))[1] == "MISS"
    await redis_client.delete(f"{cache.prefix}:version")
    assert await cache.get_or_load("customers", QUERY, ADAPTER, load) == (
        stats("9.75"),
        "MISS",
    )


async def test_shorter_ttl_does_not_reuse_longer_lived_entries(
    cache: AnalyticsCache, settings: Settings, redis_client: Redis
) -> None:
    load = AsyncMock(side_effect=[stats("1.25"), stats("9.75")])
    assert await cache.get_or_load("customers", QUERY, ADAPTER, load) == (
        stats("1.25"),
        "MISS",
    )
    shorter = AnalyticsCache(
        settings.model_copy(update={"analytics_cache_ttl": 1}),
        redis_client,
        cache_counter(CollectorRegistry()),
    )
    assert await shorter.get_or_load("customers", QUERY, ADAPTER, load) == (
        stats("9.75"),
        "MISS",
    )


async def test_wait_timeout_does_not_launch_a_second_query(
    settings: Settings, redis_client: Redis
) -> None:
    cache = AnalyticsCache(
        settings.model_copy(update={"analytics_cache_wait": 0.05}),
        redis_client,
        cache_counter(CollectorRegistry()),
    )
    started = asyncio.Event()
    release = asyncio.Event()

    async def load() -> CustomerStats:
        started.set()
        await release.wait()
        return stats("1.25")

    leader = asyncio.create_task(cache.get_or_load("customers", QUERY, ADAPTER, load))
    follower_load = AsyncMock(return_value=stats("9.75"))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        with pytest.raises(CacheBusy):
            await cache.get_or_load("customers", QUERY, ADAPTER, follower_load)
        follower_load.assert_not_awaited()
    finally:
        release.set()
        await asyncio.wait_for(leader, timeout=2)


async def test_failed_loader_releases_lock(cache: AnalyticsCache) -> None:
    with pytest.raises(RuntimeError, match="failed query"):
        await cache.get_or_load(
            "customers",
            QUERY,
            ADAPTER,
            AsyncMock(side_effect=RuntimeError("failed query")),
        )
    assert await cache.get_or_load(
        "customers", QUERY, ADAPTER, AsyncMock(return_value=stats("1.25"))
    ) == (stats("1.25"), "MISS")


@pytest.mark.parametrize("operation", ["sales", "top-products", "customers"])
async def test_analytics_routes_keep_the_same_body_on_hit(
    cache: AnalyticsCache, settings: Settings, db_session: AsyncSession, operation: str
) -> None:
    app = create_app(settings)

    async def database():
        yield db_session

    async def analytics_cache():
        return cache

    app.dependency_overrides[get_db_session] = database
    app.dependency_overrides[get_analytics_cache] = analytics_cache
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://localhost"
    ) as client:
        path = f"/api/v1/optimized/analytics/{operation}"
        first = await client.get(path)
        connection = await db_session.connection()
        statements = []

        def capture(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(connection.sync_connection, "before_cursor_execute", capture)
        try:
            second = await client.get(path)
        finally:
            event.remove(connection.sync_connection, "before_cursor_execute", capture)
    assert first.status_code == second.status_code == 200
    assert first.headers["X-Cache-Status"] == "MISS"
    assert second.headers["X-Cache-Status"] == "HIT"
    assert first.json() == second.json()
    assert statements == []
