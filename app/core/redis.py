from hashlib import sha256
from uuid import uuid4

from fastapi import Request
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from app.core.config import Settings


def create_redis(settings: Settings) -> Redis:
    return Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        decode_responses=True,
        socket_connect_timeout=settings.redis_timeout,
        socket_timeout=settings.redis_timeout,
        retry=Retry(NoBackoff(), 0),
    )


async def get_redis(request: Request) -> Redis:
    return request.app.state.redis


def analytics_cache_prefix(settings: Settings) -> str:
    identity = f"{settings.analytics_cache_namespace}\0{settings.postgres_db}"
    return f"performance_lab:analytics:v1:{sha256(identity.encode()).hexdigest()}"


async def invalidate_analytics_cache(redis: Redis, settings: Settings) -> None:
    await redis.set(f"{analytics_cache_prefix(settings)}:version", uuid4().hex)
