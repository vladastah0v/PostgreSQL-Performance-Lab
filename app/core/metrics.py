import os
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from time import perf_counter

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_client.metrics_core import (
    CounterMetricFamily,
    GaugeMetricFamily,
    Metric,
)
from prometheus_client.multiprocess import MultiProcessCollector, mark_process_dead
from sqlalchemy import event
from sqlalchemy.pool import Pool
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.profiling import is_api_request

HTTP_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
HTTP_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 10, 20, 30)
SQL_BUCKETS = (0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30)


class LabMetrics:
    def __init__(self, registry: CollectorRegistry):
        self.requests = Counter(
            "lab_http_requests",
            "Completed API requests.",
            ("method", "route", "status"),
            registry=registry,
        )
        self.http_duration = Histogram(
            "lab_http_request_duration_seconds",
            "Full API response duration in seconds.",
            ("method", "route"),
            buckets=HTTP_BUCKETS,
            registry=registry,
        )
        self.sql_duration = Histogram(
            "lab_sql_query_duration_seconds",
            "Database cursor execution time in seconds.",
            ("outcome",),
            buckets=SQL_BUCKETS,
            registry=registry,
        )
        self.pool_in_use = Gauge(
            "lab_db_pool_checked_out",
            "Connections currently checked out from application pools.",
            multiprocess_mode="livesum",
            registry=registry,
        )
        self.pool_capacity = Gauge(
            "lab_db_pool_capacity",
            "Maximum connections across application pools, including overflow.",
            multiprocess_mode="livesum",
            registry=registry,
        )

    def observe_sql(self, seconds: float, outcome: str) -> None:
        self.sql_duration.labels(outcome).observe(seconds)

    def track_pool(self, pool: Pool, capacity: int) -> None:
        self.pool_capacity.set(capacity)

        @event.listens_for(pool, "checkout")
        def checkout(connection, record, proxy):
            record.record_info["lab_metrics_checked_out"] = True
            self.pool_in_use.inc()

        @event.listens_for(pool, "checkin")
        @event.listens_for(pool, "detach")
        def release(connection, record):
            if record.record_info.pop("lab_metrics_checked_out", False):
                self.pool_in_use.dec()


class HttpMetricsMiddleware:
    def __init__(self, app: ASGIApp, metrics: LabMetrics):
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not is_api_request(scope):
            await self.app(scope, receive, send)
            return

        started = perf_counter()
        status = 500

        async def capture_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, capture_status)
        finally:
            method = scope["method"]
            method = method if method in HTTP_METHODS else "OTHER"
            route = getattr(scope.get("route"), "path", "<unmatched>")
            self.metrics.requests.labels(method, route, str(status)).inc()
            self.metrics.http_duration.labels(method, route).observe(
                perf_counter() - started
            )


class ContainerResources:
    def collect(self) -> Iterator[Metric]:
        cgroup = Path("/sys/fs/cgroup")
        try:
            cpu = dict(
                line.split() for line in (cgroup / "cpu.stat").read_text().splitlines()
            )
            usage_seconds = int(cpu["usage_usec"]) / 1_000_000
        except (OSError, KeyError, ValueError):
            pass
        else:
            yield CounterMetricFamily(
                "lab_container_cpu_seconds",
                "API container CPU time.",
                value=usage_seconds,
            )
        try:
            memory_bytes = int((cgroup / "memory.current").read_text())
        except (OSError, ValueError):
            pass
        else:
            yield GaugeMetricFamily(
                "lab_container_memory_bytes",
                "API container memory usage.",
                value=memory_bytes,
            )


def export_metrics(registry: CollectorRegistry, resources: bool) -> bytes:
    if directory := os.getenv("PROMETHEUS_MULTIPROC_DIR"):
        # Uvicorn can replace a killed worker without running its shutdown hooks.
        if Path("/proc/self").exists():
            for shard in Path(directory).glob("gauge_livesum_*.db"):
                pid = int(shard.stem.rsplit("_", 1)[1])
                if not Path(f"/proc/{pid}").exists():
                    with suppress(FileNotFoundError):
                        mark_process_dead(pid, directory)
        registry = CollectorRegistry()
        MultiProcessCollector(registry, path=directory)
        if resources:
            registry.register(ContainerResources())
    return generate_latest(registry)
