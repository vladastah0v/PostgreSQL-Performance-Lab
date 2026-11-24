from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from os import getpid
from time import perf_counter_ns

from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import AsyncAdaptedQueuePool
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


@dataclass
class RequestProfile:
    sql_count: int = 0
    sql_ns: int = 0
    pool_ns: int = 0
    endpoint_ns: int = 0
    serialization_ns: int = 0
    cache_ns: int = 0

    def server_timing(self, server_ns: int) -> str:
        durations = {
            "server": server_ns,
            "sql": self.sql_ns,
            "pool": self.pool_ns,
            "serialization": self.serialization_ns,
            "cache": self.cache_ns,
            "logic": max(
                0, self.endpoint_ns - self.sql_ns - self.pool_ns - self.cache_ns
            ),
            "framework": max(0, server_ns - self.endpoint_ns - self.serialization_ns),
        }
        return ", ".join(
            f"{name};dur={value / 1_000_000:.3f}" for name, value in durations.items()
        )


current_profile: ContextVar[RequestProfile | None] = ContextVar(
    "request_profile", default=None
)


@contextmanager
def profile_request() -> Iterator[RequestProfile]:
    profile = RequestProfile()
    token = current_profile.set(profile)
    try:
        yield profile
    finally:
        current_profile.reset(token)


def is_api_request(scope: Scope) -> bool:
    return scope["type"] == "http" and scope["path"].startswith("/api/v1/")


class ProfileMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not is_api_request(scope):
            await self.app(scope, receive, send)
            return

        started = perf_counter_ns()
        with profile_request() as profile:

            async def send_profile(message: Message) -> None:
                if message["type"] == "http.response.start":
                    server_ns = perf_counter_ns() - started
                    headers = MutableHeaders(raw=message.setdefault("headers", []))
                    headers.append("Server-Timing", profile.server_timing(server_ns))
                    headers["X-SQL-Queries"] = str(profile.sql_count)
                    headers["X-Worker-Pid"] = str(getpid())
                await send(message)

            await self.app(scope, receive, send_profile)


class OperationSpanProcessor(SpanProcessor):
    def on_end(self, span: ReadableSpan) -> None:
        profile = current_profile.get()
        if profile is None or span.start_time is None or span.end_time is None:
            return
        duration = max(0, span.end_time - span.start_time)
        if span.name == "fastapi.endpoint":
            profile.endpoint_ns += duration
        elif span.name == "fastapi.serialization":
            profile.serialization_ns += duration


def create_profile_tracer() -> TracerProvider:
    provider = TracerProvider(sampler=ALWAYS_ON, shutdown_on_exit=False)
    provider.add_span_processor(OperationSpanProcessor())
    return provider


class ProfiledPool(AsyncAdaptedQueuePool):
    def connect(self):
        profile = current_profile.get()
        if profile is None:
            return super().connect()
        started = perf_counter_ns()
        try:
            return super().connect()
        finally:
            profile.pool_ns += perf_counter_ns() - started


def install_sql_timers(engine: Engine) -> None:
    @event.listens_for(engine, "before_cursor_execute")
    def start_sql(connection, cursor, statement, parameters, context, executemany):
        profile = current_profile.get()
        if profile is not None:
            profile.sql_count += 1
            context.lab_query_started = perf_counter_ns()

    def end_sql(context) -> None:
        profile = current_profile.get()
        started = getattr(context, "lab_query_started", None)
        if profile is not None and started is not None:
            profile.sql_ns += perf_counter_ns() - started
            context.lab_query_started = None

    @event.listens_for(engine, "after_cursor_execute")
    def finish_sql(connection, cursor, statement, parameters, context, executemany):
        end_sql(context)

    @event.listens_for(engine, "handle_error")
    def finish_failed_sql(exception_context):
        end_sql(exception_context.execution_context)
