import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Annotated

from asyncpg import PostgresError
from fastapi import Depends, FastAPI, HTTPException, status
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.baseline.routes import router as baseline_router
from app.core.config import Settings
from app.core.database import create_engine, get_db_session
from app.core.exceptions import ResourceNotFound, database_error, resource_not_found
from app.core.lab_access import docker_gateway
from app.core.redis import create_redis, get_redis

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as resources:
            engine = create_engine(settings)
            resources.push_async_callback(engine.dispose)
            app.state.db_session_factory = async_sessionmaker(
                engine, expire_on_commit=False
            )

            redis = create_redis(settings)
            resources.push_async_callback(redis.aclose)
            app.state.redis = redis
            yield

    app = FastAPI(title="PostgreSQL Performance Lab", lifespan=lifespan)
    app.add_exception_handler(ResourceNotFound, resource_not_found)
    app.add_exception_handler(SQLAlchemyError, database_error)
    if settings.lab_mode:
        app.state.lab_gateway = (
            docker_gateway() if settings.lab_allow_docker_gateway else None
        )
        app.include_router(baseline_router)

    @app.get("/health/live", tags=["health"])
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    async def ready(
        session: Annotated[AsyncSession, Depends(get_db_session)],
        redis: Annotated[Redis, Depends(get_redis)],
    ) -> dict[str, str]:
        try:
            async with asyncio.timeout(settings.healthcheck_timeout):
                await session.execute(text("SELECT 1"))
                if await redis.ping():
                    return {"status": "ok"}
        except (OSError, PostgresError, RedisError, SQLAlchemyError) as exc:
            logger.warning("Readiness check failed (%s)", type(exc).__name__)

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="A required dependency is unavailable",
        )

    return app


app = create_app()
