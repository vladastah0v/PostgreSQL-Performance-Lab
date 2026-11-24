import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from hashlib import sha256
from time import monotonic, perf_counter_ns
from typing import Literal
from uuid import uuid4

from prometheus_client import CollectorRegistry, Counter
from pydantic import BaseModel, TypeAdapter, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.core.profiling import current_profile
from app.core.redis import analytics_cache_prefix

type Operation = Literal["sales", "top-products", "customers"]

ACQUIRE = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
    return {'retry'}
end
local value = redis.call('GET', KEYS[2])
if value then
    return {'hit', value}
end
if redis.call('SET', KEYS[3], ARGV[2], 'NX', 'PX', ARGV[3]) then
    local now = redis.call('TIME')
    return {'miss', now[1] * 1000 + math.floor(now[2] / 1000)}
end
return {'wait'}
"""

PUBLISH = """
if redis.call('GET', KEYS[3]) ~= ARGV[2] then
    return 0
end
if redis.call('GET', KEYS[1]) == ARGV[1] then
    local now = redis.call('TIME')
    if tonumber(ARGV[4]) > now[1] * 1000 + math.floor(now[2] / 1000) then
        redis.call('SET', KEYS[2], ARGV[3], 'PXAT', ARGV[4])
    end
end
return redis.call('DEL', KEYS[3])
"""

COMPARE_DELETE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class CacheBusy(RuntimeError):
    pass


def cache_counter(registry: CollectorRegistry) -> Counter:
    counter = Counter(
        "analytics_cache_requests",
        "Lookup outcomes for optimized analytics requests.",
        ("operation", "outcome"),
        registry=registry,
    )
    for operation in ("sales", "top-products", "customers"):
        for outcome in ("hit", "miss"):
            counter.labels(operation, outcome)
    return counter


class AnalyticsCache:
    def __init__(self, settings: Settings, redis: Redis, requests: Counter):
        self.redis = redis
        self.enabled = settings.analytics_cache_enabled
        self.prefix = analytics_cache_prefix(settings)
        self.requests = requests
        self.wait_seconds = settings.analytics_cache_wait
        self.ttl_ms = settings.analytics_cache_ttl * 1_000
        self.lease_ms = max(
            60_000,
            settings.db_statement_timeout_ms
            + int((settings.db_pool_timeout + self.wait_seconds + 5) * 1_000),
        )

    async def version(self) -> str | None:
        key = f"{self.prefix}:version"
        current = await self.redis.get(key)
        if current is not None:
            return current
        candidate = uuid4().hex
        if await self.redis.set(key, candidate, nx=True):
            return candidate
        return await self.redis.get(key)

    async def get_or_load[T](
        self,
        operation: Operation,
        query: BaseModel,
        adapter: TypeAdapter[T],
        load: Callable[[], Awaitable[T]],
    ) -> tuple[T, str]:
        if not self.enabled:
            self.requests.labels(operation, "bypass").inc()
            return await load(), "BYPASS"

        profile = current_profile.get()
        if profile is None:
            return await self._get_or_load(operation, query, adapter, load)

        started = perf_counter_ns()
        source_ns = 0

        async def timed_load() -> T:
            nonlocal source_ns
            load_started = perf_counter_ns()
            try:
                return await load()
            finally:
                source_ns += perf_counter_ns() - load_started

        try:
            return await self._get_or_load(operation, query, adapter, timed_load)
        finally:
            profile.cache_ns += perf_counter_ns() - started - source_ns

    async def _get_or_load[T](
        self,
        operation: Operation,
        query: BaseModel,
        adapter: TypeAdapter[T],
        load: Callable[[], Awaitable[T]],
    ) -> tuple[T, str]:

        digest = sha256(query.model_dump_json().encode()).hexdigest()
        token = uuid4().hex
        deadline = monotonic() + self.wait_seconds
        while True:
            try:
                version = await self.version()
                if version is not None:
                    key = f"{self.prefix}:{version}:{self.ttl_ms}:{operation}:{digest}"
                    keys = (f"{self.prefix}:version", key, f"{key}:lock")
                    outcome, *payload = await self.redis.eval(
                        ACQUIRE, 3, *keys, version, token, self.lease_ms
                    )
                    if outcome == "hit":
                        try:
                            cached = adapter.validate_json(payload[0])
                        except ValidationError:
                            await self.redis.eval(COMPARE_DELETE, 1, key, payload[0])
                        else:
                            self.requests.labels(operation, "hit").inc()
                            return cached, "HIT"
                    elif outcome == "miss":
                        break
            except RedisError:
                self.requests.labels(operation, "error").inc()
                return await load(), "BYPASS"

            delay = min(0.05, deadline - monotonic())
            if delay <= 0:
                self.requests.labels(operation, "busy").inc()
                raise CacheBusy
            await asyncio.sleep(delay)

        self.requests.labels(operation, "miss").inc()
        published = False
        try:
            result = await load()
            try:
                published = bool(
                    await self.redis.eval(
                        PUBLISH,
                        3,
                        *keys,
                        version,
                        token,
                        adapter.dump_json(result),
                        payload[0] + self.ttl_ms,
                    )
                )
            except RedisError:
                pass
            return result, "MISS"
        finally:
            if not published:
                with suppress(RedisError):
                    await self.redis.eval(COMPARE_DELETE, 1, keys[2], token)
