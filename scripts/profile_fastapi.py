import argparse
import asyncio
import json
import os
import platform
import subprocess
from contextlib import AsyncExitStack
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from statistics import median
from time import perf_counter, perf_counter_ns
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import create_engine
from app.core.profiling import profile_request
from app.core.redis import create_redis, invalidate_analytics_cache
from app.models import User
from app.schemas.cursors import encode_cursor
from app.schemas.parameters import OrderSearch
from app.schemas.responses import OrderSummary, UserSummary
from scripts.benchmark_analytics_cache import DATES, QUERIES, get_dataset, summarize
from scripts.generate_data import LOCAL_DATABASE_HOSTS
from scripts.run_sql_experiments import command_output, docker_metadata


@dataclass(frozen=True)
class Workload:
    path: str
    params: dict
    cache_status: str | None = None
    ignore_offset: bool = False


@dataclass(frozen=True)
class PoolConfig:
    size: int = 1
    overflow: int = 0
    timeout: float = 2
    workers: int = 1

    @property
    def max_slots(self) -> int:
        return self.workers * (self.size + self.overflow)


POOL_CONFIGS = {
    "control": PoolConfig(),
    "larger-pool": PoolConfig(size=2),
    "overflow": PoolConfig(overflow=1),
    "short-timeout": PoolConfig(timeout=0.04),
    "two-workers": PoolConfig(workers=2),
}
PHASES = ("server", "sql", "pool", "serialization", "cache", "logic", "framework")


def fingerprint(value) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode()).hexdigest()


async def request(client: httpx.AsyncClient, workload: Workload) -> dict:
    started = perf_counter()
    response = await client.get(workload.path, params=workload.params)
    http_ms = round((perf_counter() - started) * 1_000, 3)
    header = response.headers.get("Server-Timing")
    if header is None:
        raise ValueError("Start the API with API_PROFILING_ENABLED=true")
    phases = {
        name: float(value)
        for segment in header.split(",")
        for name, value in [segment.strip().split(";dur=", maxsplit=1)]
    }
    if set(phases) != set(PHASES):
        raise ValueError("Unexpected Server-Timing phases")
    data = response.json()
    cache_status = response.headers.get("X-Cache-Status")
    if (
        response.is_success
        and workload.cache_status is not None
        and cache_status != workload.cache_status
    ):
        raise ValueError(
            f"Expected {workload.cache_status} cache status, got {cache_status}"
        )
    if response.is_success and workload.ignore_offset:
        data.pop("offset")
    return {
        "status": response.status_code,
        "http_ms": http_ms,
        "transport_and_client_ms": round(max(0, http_ms - phases["server"]), 3),
        "response_bytes": len(response.content),
        "worker_pid": int(response.headers["X-Worker-Pid"]),
        "sql_queries": int(response.headers["X-SQL-Queries"]),
        "cache_status": cache_status,
        "timing_ms": phases,
        "response_sha256": fingerprint(data),
    }


async def batch(
    client: httpx.AsyncClient, workload: Workload, count: int
) -> list[dict]:
    return await asyncio.gather(*(request(client, workload) for _ in range(count)))


def metrics(samples: list[dict], *, allow_busy: bool = False) -> dict:
    accepted = {200, 503} if allow_busy else {200}
    if any(sample["status"] not in accepted for sample in samples):
        raise ValueError(
            f"Unexpected HTTP status codes: {sorted({item['status'] for item in samples})}"
        )
    successful = [sample for sample in samples if sample["status"] == 200]
    if not successful or len({sample["response_sha256"] for sample in successful}) != 1:
        raise ValueError("Expected successful responses with identical JSON")
    return {
        "success_count": len(successful),
        "status_counts": {
            str(code): sum(item["status"] == code for item in samples)
            for code in sorted({item["status"] for item in samples})
        },
        "http_success": summarize([sample["http_ms"] for sample in successful]),
        "http_all": summarize([sample["http_ms"] for sample in samples]),
        "phases_p50_ms": {
            phase: round(median(sample["timing_ms"][phase] for sample in successful), 3)
            for phase in PHASES
        },
        "transport_and_client_p50_ms": round(
            median(sample["transport_and_client_ms"] for sample in successful), 3
        ),
        "sql_queries_p50": median(sample["sql_queries"] for sample in successful),
        "response_bytes_p50": median(sample["response_bytes"] for sample in successful),
        "response_sha256": successful[0]["response_sha256"],
        "workers_observed": sorted({sample["worker_pid"] for sample in samples}),
        "runs": samples,
    }


async def compare_requests(
    client: httpx.AsyncClient,
    before: Workload,
    after: Workload,
    repetitions: int,
    concurrency: int,
) -> dict:
    await batch(client, before, concurrency)
    await batch(client, after, concurrency)
    variants = {"before": before, "after": after}
    samples = {name: [] for name in variants}
    for wave, first in enumerate(range(0, repetitions, concurrency)):
        count = min(concurrency, repetitions - first)
        for name in variants if wave % 2 == 0 else reversed(variants):
            samples[name].extend(await batch(client, variants[name], count))
    reports = {name: metrics(items) for name, items in samples.items()}
    if reports["before"]["response_sha256"] != reports["after"]["response_sha256"]:
        raise ValueError("The API variants returned different results")
    return {
        "workloads": {name: asdict(workload) for name, workload in variants.items()},
        **reports,
    }


async def latency(
    client: httpx.AsyncClient, repetitions: int, concurrency: int
) -> dict:
    def workload(
        variant: str, path: str, params: dict, cache: str | None = None
    ) -> Workload:
        return Workload(f"/api/v1/{variant}/{path}", params, cache)

    history = await compare_requests(
        client,
        workload("baseline", "users/1/orders", {"limit": 20}),
        workload("optimized", "users/1/orders", {"limit": 20}),
        repetitions,
        concurrency,
    )
    top_products = await compare_requests(
        client,
        workload("baseline", "analytics/top-products", QUERIES["top-products"]),
        workload(
            "optimized", "analytics/top-products", QUERIES["top-products"], "BYPASS"
        ),
        repetitions,
        concurrency,
    )
    order_filters = {**DATES, "status": "delivered"}
    anchor = await client.get(
        "/api/v1/optimized/orders",
        params={**order_filters, "limit": 1, "offset": 7_999},
    )
    anchor.raise_for_status()
    [previous] = anchor.json()["items"]
    order = OrderSummary.model_validate(previous)
    cursor = encode_cursor(
        OrderSearch.model_validate(order_filters), "orders", order.id, order.created_at
    )
    pagination = await compare_requests(
        client,
        Workload(
            "/api/v1/optimized/orders",
            {**order_filters, "limit": 20, "offset": 8_000},
            ignore_offset=True,
        ),
        Workload(
            "/api/v1/optimized/orders",
            {**order_filters, "limit": 20, "cursor": cursor},
            ignore_offset=True,
        ),
        repetitions,
        concurrency,
    )
    return {"history": history, "top-products": top_products, "pagination": pagination}


async def connection_budget(settings: Settings, config: PoolConfig) -> dict:
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text("""
                SELECT current_setting('max_connections')::int AS max_connections,
                       current_setting('superuser_reserved_connections')::int AS superuser_reserved,
                       current_setting('reserved_connections')::int AS reserved,
                       (SELECT count(*) FROM pg_stat_activity
                        WHERE backend_type = 'client backend' AND pid <> pg_backend_pid()) AS other_connections
            """)
                    )
                )
                .mappings()
                .one()
            )
    finally:
        await engine.dispose()
    budget = dict(row)
    budget["required_with_existing"] = config.max_slots + row["other_connections"]
    budget["usable"] = (
        row["max_connections"] - row["superuser_reserved"] - row["reserved"]
    )
    if budget["required_with_existing"] > budget["usable"]:
        raise ValueError(
            "Pool configuration exceeds the available PostgreSQL connection budget"
        )
    return budget


def configure_api(config: PoolConfig | None) -> None:
    settings = dict(os.environ)
    if config is not None:
        settings.update(
            API_PROFILING_ENABLED="true",
            ANALYTICS_CACHE_ENABLED="false",
            DB_POOL_SIZE=str(config.size),
            DB_MAX_OVERFLOW=str(config.overflow),
            DB_POOL_TIMEOUT=str(config.timeout),
            WEB_CONCURRENCY=str(config.workers),
        )
    subprocess.run(
        [
            "docker",
            "compose",
            "up",
            "-d",
            "--no-build",
            "--wait",
            "--wait-timeout",
            "60",
            "api",
        ],
        env=settings,
        check=True,
        timeout=90,
    )


async def pools(
    base_url: str, settings: Settings, repetitions: int, concurrency: int
) -> dict:
    workload = Workload(
        "/api/v1/optimized/analytics/sales", {**DATES, "period": "day"}, "BYPASS"
    )
    reports = {}
    async with AsyncExitStack() as restore:
        for name, config in POOL_CONFIGS.items():
            print(f"Measuring {name}", flush=True)
            budget = await connection_budget(settings, config)
            if not reports:
                restore.push_async_callback(asyncio.to_thread, configure_api, None)
            await asyncio.to_thread(configure_api, config)
            async with httpx.AsyncClient(
                base_url=base_url,
                timeout=40,
                limits=httpx.Limits(max_keepalive_connections=0),
                trust_env=False,
            ) as client:
                for _ in range(2):
                    await batch(client, workload, concurrency)
                samples = []
                for first in range(0, repetitions, concurrency):
                    samples.extend(
                        await batch(
                            client, workload, min(concurrency, repetitions - first)
                        )
                    )
            report = metrics(samples, allow_busy=name == "short-timeout")
            if len(report["workers_observed"]) != config.workers:
                raise ValueError(
                    f"Expected responses from {config.workers} workers in {name}"
                )
            if (
                reports
                and report["response_sha256"] != reports["control"]["response_sha256"]
            ):
                raise ValueError(
                    f"Successful responses changed in pool configuration {name}"
                )
            reports[name] = {
                "config": asdict(config),
                "max_slots": config.max_slots,
                "budget": budget,
                **report,
            }
    return {"workload": asdict(workload), "configurations": reports}


async def orm(settings: Settings, repetitions: int) -> dict:
    engine = create_engine(settings.model_copy(update={"api_profiling_enabled": True}))
    orm_query = select(User).order_by(User.id).limit(1_000)
    core_query = select(User.__table__).order_by(User.id).limit(1_000)
    if str(orm_query) != str(core_query):
        raise ValueError("ORM and Core must execute the same SQL")
    samples = {"orm": [], "core": []}
    fingerprints = set()
    try:
        async with engine.connect() as connection:
            for repeat in range(repetitions + 1):
                for name in samples if repeat % 2 == 0 else reversed(samples):
                    async with AsyncSession(bind=connection) as session:
                        started = perf_counter_ns()
                        with profile_request() as profile:
                            if name == "orm":
                                rows = (await session.scalars(orm_query)).all()
                            else:
                                rows = (
                                    (await session.execute(core_query)).mappings().all()
                                )
                            summaries = [
                                UserSummary.model_validate(row) for row in rows
                            ]
                        elapsed = perf_counter_ns() - started
                    if len(summaries) != 1_000 or profile.sql_count != 1:
                        raise ValueError("Expected one SELECT returning 1,000 users")
                    fingerprints.add(
                        fingerprint(
                            [user.model_dump(mode="json") for user in summaries]
                        )
                    )
                    if repeat:
                        samples[name].append(
                            {
                                "total_ms": round(elapsed / 1e6, 3),
                                "sql_ms": round(profile.sql_ns / 1e6, 3),
                                "application_ms": round(
                                    max(0, elapsed - profile.sql_ns) / 1e6, 3
                                ),
                            }
                        )
    finally:
        await engine.dispose()
    if len(fingerprints) != 1:
        raise ValueError("ORM and Core returned different values")
    return {
        "sql": str(orm_query),
        "rows": 1_000,
        "response_sha256": fingerprints.pop(),
        "measurements": {
            name: {
                "runs": runs,
                "median_ms": {
                    key: round(median(item[key] for item in runs), 3) for key in runs[0]
                },
            }
            for name, runs in samples.items()
        },
    }


async def cache(
    client: httpx.AsyncClient, settings: Settings, repetitions: int, concurrency: int
) -> dict:
    path = "/api/v1/optimized/analytics/top-products"
    async with create_redis(settings) as redis:
        await invalidate_analytics_cache(redis, settings)
    prime = await request(client, Workload(path, QUERIES["top-products"], "MISS"))
    workload = Workload(path, QUERIES["top-products"], "HIT")
    samples = []
    for first in range(0, repetitions, concurrency):
        samples.extend(
            await batch(client, workload, min(concurrency, repetitions - first))
        )
    report = metrics(samples)
    if prime["response_sha256"] != report["response_sha256"] or any(
        item["sql_queries"] for item in samples
    ):
        raise ValueError(
            "Expected cached responses with the same JSON and zero SQL queries"
        )
    return {"workload": asdict(workload), "prime": prime, "warm": report}


async def run(args: argparse.Namespace) -> dict:
    settings = Settings()
    if settings.postgres_host not in LOCAL_DATABASE_HOSTS:
        raise ValueError("Profiling requires a local laboratory database")
    metadata = {
        "started_at_utc": datetime.now(UTC).isoformat(),
        "git_commit": command_output("git", "rev-parse", "HEAD"),
        "git_worktree_dirty": bool(command_output("git", "status", "--porcelain")),
        "python": platform.python_version(),
        "docker": docker_metadata(),
        "dataset": await get_dataset(settings),
        "repetitions": args.repetitions,
        "concurrency": 1 if args.scenario == "orm" else args.concurrency,
        "api_settings": {
            "workers": settings.web_concurrency,
            "pool_size": settings.db_pool_size,
            "max_overflow": settings.db_max_overflow,
            "pool_timeout": settings.db_pool_timeout,
            "cache_enabled": settings.analytics_cache_enabled,
        },
    }
    if args.scenario == "pool":
        result = await pools(
            args.base_url, settings, args.repetitions, args.concurrency
        )
    elif args.scenario == "orm":
        result = await orm(settings, args.repetitions)
    else:
        async with httpx.AsyncClient(
            base_url=args.base_url, timeout=40, trust_env=False
        ) as client:
            if args.scenario == "latency":
                result = await latency(client, args.repetitions, args.concurrency)
            else:
                result = await cache(
                    client, settings, args.repetitions, args.concurrency
                )
    return {"metadata": metadata, "results": result}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Profile the local FastAPI laboratory and PostgreSQL client."
    )
    parser.add_argument(
        "--scenario", choices=("latency", "pool", "orm", "cache"), required=True
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8008")
    parser.add_argument("--repetitions", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    address = urlsplit(args.base_url)
    if address.scheme != "http" or address.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        parser.error("--base-url must point to the local HTTP API")
    if not 1 <= args.concurrency <= min(16, args.repetitions) or args.repetitions > 100:
        parser.error(
            "Use 1–100 repetitions and 1–16 concurrent requests, up to the repetition count"
        )
    output = args.output / f"{args.scenario}.json"
    if output.exists():
        parser.error(f"{output} already exists")
    report = asyncio.run(run(args))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as file:
        json.dump(report, file, indent=2)
        file.write("\n")
    print(output)


if __name__ == "__main__":
    main()
