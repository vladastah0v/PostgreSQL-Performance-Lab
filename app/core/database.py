from collections.abc import AsyncIterator

from asyncpg import PostgresError
from fastapi import Request
from sqlalchemy import MetaData, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import Settings
from app.core.metrics import LabMetrics
from app.core.profiling import ProfiledPool, install_sql_timers


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def create_engine(settings: Settings, metrics: LabMetrics | None = None) -> AsyncEngine:
    engine = create_async_engine(
        settings.database_url,
        poolclass=ProfiledPool if settings.api_profiling_enabled else None,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_pre_ping=True,
        connect_args={
            "timeout": settings.db_connect_timeout,
            "server_settings": {
                "statement_timeout": str(settings.db_statement_timeout_ms),
                "timezone": "UTC",
            },
        },
    )

    @event.listens_for(engine.sync_engine, "do_connect")
    def connect(dialect, connection_record, args, kwargs):
        try:
            return dialect.connect(*args, **kwargs)
        except (OSError, PostgresError) as error:
            raise OperationalError(
                None, None, error, connection_invalidated=True
            ) from error

    if settings.api_profiling_enabled or metrics is not None:
        install_sql_timers(engine.sync_engine, metrics.observe_sql if metrics else None)
    if metrics is not None:
        metrics.track_pool(
            engine.pool, settings.db_pool_size + settings.db_max_overflow
        )
    return engine


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.db_session_factory() as session:
        yield session
