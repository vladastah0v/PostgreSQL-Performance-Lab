import argparse
import asyncio
import csv
import json
import math
import platform
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from statistics import median
from time import perf_counter
from urllib.parse import urlsplit

import httpx
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.core.database import create_engine
from app.core.redis import create_redis, invalidate_analytics_cache
from scripts.run_sql_experiments import command_output, docker_metadata

MODES = {"disabled": "BYPASS", "cold": "MISS", "warm": "HIT"}
DATES = {"date_from": "2025-01-01", "date_to": "2025-12-31"}
QUERIES = {
    "sales": {**DATES, "period": "month"},
    "top-products": {**DATES, "limit": 20},
    "customers": DATES,
}


async def get_dataset(settings: Settings) -> dict:
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            dataset = await connection.scalar(
                text("SELECT document FROM lab_data.dataset_manifest")
            )
            if dataset is None:
                raise ValueError("Load the synthetic dataset before benchmarking")
            return {
                "manifest": dataset,
                "name": await connection.scalar(text("SELECT current_database()")),
                "revision": await connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                ),
                "postgresql": await connection.scalar(text("SHOW server_version")),
            }
    finally:
        await engine.dispose()


def response_fingerprint(response: httpx.Response, expected_status: str) -> str:
    response.raise_for_status()
    actual = response.headers.get("X-Cache-Status")
    if actual != expected_status:
        raise ValueError(
            f"Expected cache status {expected_status}, got {actual} for {response.url}"
        )
    return sha256(
        json.dumps(response.json(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def summarize(samples: list[float]) -> dict:
    ordered = sorted(samples)
    return {
        "p50_ms": round(median(ordered), 3),
        "p95_ms": ordered[math.ceil(len(ordered) * 0.95) - 1],
        "p99_ms": ordered[math.ceil(len(ordered) * 0.99) - 1],
        "min_ms": ordered[0],
        "max_ms": ordered[-1],
    }


async def run(mode: str, base_url: str, repetitions: int) -> dict:
    settings = Settings()
    dataset = await get_dataset(settings)
    started_at = datetime.now(UTC).isoformat()
    operations = {}
    async with (
        create_redis(settings) as redis,
        httpx.AsyncClient(base_url=base_url, timeout=35) as client,
    ):
        redis_version = (
            (await redis.info("server"))["redis_version"]
            if mode != "disabled"
            else None
        )
        for name, params in QUERIES.items():
            path = f"/api/v1/optimized/analytics/{name}"
            if mode != "disabled":
                await invalidate_analytics_cache(redis, settings)
            warmup = await client.get(path, params=params)
            fingerprint = response_fingerprint(
                warmup, "BYPASS" if mode == "disabled" else "MISS"
            )
            runs = []
            for repeat in range(1, repetitions + 1):
                if mode == "cold":
                    await invalidate_analytics_cache(redis, settings)
                started = perf_counter()
                response = await client.get(path, params=params)
                delay = round((perf_counter() - started) * 1000, 3)
                if response_fingerprint(response, MODES[mode]) != fingerprint:
                    raise ValueError(f"Response changed while measuring {name}")
                runs.append(
                    {
                        "repetition": repeat,
                        "cache_status": MODES[mode],
                        "latency_ms": delay,
                    }
                )
            operations[name] = {
                "parameters": params,
                "response_sha256": fingerprint,
                "runs": runs,
                **summarize([item["latency_ms"] for item in runs]),
            }
            print(f"{mode} {name} p50={operations[name]['p50_ms']} ms", flush=True)

        metrics = await client.get("/metrics")
        metrics.raise_for_status()
    return {
        "mode": mode,
        "started_at_utc": started_at,
        "dataset": dataset,
        "repetitions": repetitions,
        "python": platform.python_version(),
        "git_commit": command_output("git", "rev-parse", "HEAD"),
        "git_worktree_dirty": bool(command_output("git", "status", "--porcelain")),
        "docker": docker_metadata(),
        "redis_version": redis_version,
        "analytics_cache_ttl_seconds": settings.analytics_cache_ttl,
        "concurrency": 1,
        "operations": operations,
        "cache_counters": [
            line
            for line in metrics.text.splitlines()
            if line.startswith("analytics_cache_requests_total{")
        ],
    }


def write_comparison(directory: Path) -> None:
    paths = [directory / f"{mode}.json" for mode in MODES]
    if not all(path.exists() for path in paths):
        return
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    reference = reports[0]
    rows = []
    for report in reports:
        if (
            report["dataset"] != reference["dataset"]
            or report["repetitions"] != reference["repetitions"]
        ):
            raise ValueError(
                "Cache reports use different datasets or repetition counts"
            )
        for operation, results in report["operations"].items():
            original = reference["operations"][operation]
            if any(
                results[field] != original[field]
                for field in ("parameters", "response_sha256")
            ):
                raise ValueError(
                    f"Cache modes returned different results for {operation}"
                )
            rows.append(
                {
                    "mode": report["mode"],
                    "operation": operation,
                    **{
                        key: results[key]
                        for key in ("p50_ms", "p95_ms", "p99_ms", "min_ms", "max_ms")
                    },
                }
            )
    with (directory / "comparison.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare cache modes on the local Optimized analytics API."
    )
    parser.add_argument("--mode", choices=MODES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8008")
    parser.add_argument("--repetitions", type=int, default=20)
    args = parser.parse_args()
    address = urlsplit(args.base_url)
    if address.scheme != "http" or address.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        parser.error("--base-url must point to the local HTTP API")
    if not 1 <= args.repetitions <= 100:
        parser.error("--repetitions must be between 1 and 100")
    output = args.output / f"{args.mode}.json"
    if output.exists():
        parser.error(f"{output} already exists. Choose a new benchmark directory.")
    try:
        result = asyncio.run(run(args.mode, args.base_url, args.repetitions))
        args.output.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as file:
            json.dump(result, file, indent=2)
            file.write("\n")
        write_comparison(args.output)
    except (httpx.HTTPError, OSError, RedisError, SQLAlchemyError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
