import argparse
import asyncio
import csv
import json
import platform
import subprocess
import sys
import tomllib
from collections import defaultdict
from contextlib import suppress
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from statistics import median
from urllib.parse import urlsplit

import httpx
import psutil
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.core.database import create_engine
from benchmarks.metrics import (
    docker_bytes,
    resources_during_measurement,
    workload_metrics,
)
from benchmarks.scenarios.analytics import SCENARIOS, LoadConfig, select_scenarios
from scripts.benchmark_analytics_cache import get_dataset
from scripts.generate_data import LOCAL_DATABASE_HOSTS, PROFILES
from scripts.profile_fastapi import PoolConfig, connection_budget
from scripts.run_sql_experiments import command_output, docker_metadata, write_json

PROFILE_CONFIG = (
    Path(__file__).resolve().parents[1]
    / "benchmarks"
    / "configs"
    / "load-profiles.toml"
)
VARIANTS = ("baseline", "optimized")
SERVICES = ("api", "postgres", "redis")
CONNECTIONS = text("""
    SELECT count(*) AS total,
           count(*) FILTER (WHERE state = 'active') AS active,
           count(*) FILTER (WHERE state <> 'idle') AS busy
    FROM pg_stat_activity
    WHERE datname = current_database()
      AND backend_type = 'client backend'
      AND pid <> pg_backend_pid()
""")


def require_command(*command: str) -> str:
    try:
        result = subprocess.check_output(
            command, text=True, stderr=subprocess.PIPE, timeout=20
        ).strip()
    except subprocess.SubprocessError as error:
        raise ValueError(
            f"Could not read the running lab via {' '.join(command[:3])} ({type(error).__name__})"
        ) from error
    if not result:
        raise ValueError(f"Could not read the running lab via {' '.join(command[:3])}")
    return result


def runtime_settings(host: str) -> dict:
    published = require_command("docker", "compose", "port", "api", "8000")
    if (urlsplit(host).port or 80) != int(published.rsplit(":", maxsplit=1)[1]):
        raise ValueError(
            "--host does not match the published loopback port of the Compose API"
        )
    code = (
        "from app.core.config import Settings; "
        "print(Settings().model_dump_json(include={"
        "'lab_mode','analytics_cache_enabled','web_concurrency','db_pool_size','db_max_overflow',"
        "'db_pool_timeout','db_statement_timeout_ms','api_profiling_enabled','postgres_db'}))"
    )
    result = json.loads(
        require_command("docker", "compose", "exec", "-T", "api", "python", "-c", code)
    )
    if not result["lab_mode"] or result["analytics_cache_enabled"]:
        raise ValueError(
            "Start the Compose API with LAB_MODE=true and ANALYTICS_CACHE_ENABLED=false"
        )
    return result


async def verify_responses(config: LoadConfig) -> dict[str, int]:
    counts = {}
    async with httpx.AsyncClient(
        base_url=config.host, timeout=config.request_timeout, trust_env=False
    ) as client:
        for scenario in select_scenarios(config.scenario):
            returned = []
            for variant in VARIANTS:
                response = await client.get(
                    scenario.url(variant), params=scenario.parameters
                )
                response.raise_for_status()
                if (
                    expected := scenario.cache_status(variant)
                ) is not None and response.headers.get("X-Cache-Status") != expected:
                    raise ValueError(f"Unexpected caching on {scenario.name}")
                returned.append(response.json())
            if returned[0] != returned[1]:
                raise ValueError(f"Different JSON for {scenario.name}")
            result = returned[0]
            counts[scenario.name] = len(
                result.get("items", [result]) if isinstance(result, dict) else result
            )
    return counts


async def pg_connections(engine: AsyncEngine) -> dict:
    async with engine.connect() as connection:
        result = await connection.execute(CONNECTIONS)
        return dict(result.mappings().one())


def container_stats(services: dict[str, str]) -> dict:
    data = command_output(
        "docker", "stats", "--no-stream", "--format", "{{json .}}", *services.values()
    )
    if not data:
        raise RuntimeError("Docker returned no resource statistics")
    rows = {}
    for line in data.splitlines():
        item = json.loads(line)
        short_id = item["ID"]
        service = next(
            name
            for name, identifier in services.items()
            if identifier.startswith(short_id)
        )
        used, capacity = item["MemUsage"].split(" / ")
        rows[service] = {
            "cpu_pct": float(item["CPUPerc"].rstrip("%")),
            "memory_bytes": docker_bytes(used),
            "memory_limit_bytes": docker_bytes(capacity),
        }
    return rows


def generator_stats(process: psutil.Process) -> dict | None:
    try:
        return {
            "cpu_pct": process.cpu_percent(),
            "memory_bytes": process.memory_info().rss,
        }
    except psutil.Error:
        return None


async def watch_resources(
    engine: AsyncEngine, services: dict[str, str], pid: int, done: asyncio.Event
) -> list[dict]:
    snapshots = []
    process = None
    with suppress(psutil.Error):
        process = psutil.Process(pid)
        process.cpu_percent()
    while not done.is_set():
        containers, connections = await asyncio.gather(
            asyncio.to_thread(container_stats, services),
            pg_connections(engine),
            return_exceptions=True,
        )
        snapshots.append(
            {
                "sampled_at_utc": datetime.now(UTC).isoformat(),
                "containers": containers if isinstance(containers, dict) else {},
                "postgres_connections": connections
                if isinstance(connections, dict)
                else None,
                "locust": generator_stats(process) if process is not None else None,
                "errors": [
                    type(result).__name__
                    for result in (containers, connections)
                    if isinstance(result, Exception)
                ],
            }
        )
        try:
            await asyncio.wait_for(done.wait(), timeout=1)
        except TimeoutError:
            pass
    return snapshots


async def wait_for_idle(engine: AsyncEngine, timeout: float) -> None:
    idle_hits = 0
    async with asyncio.timeout(timeout):
        while idle_hits < 2:
            snapshot = await pg_connections(engine)
            idle_hits = idle_hits + 1 if snapshot["busy"] == 0 else 0
            if idle_hits < 2:
                await asyncio.sleep(0.25)


async def run_trial(
    engine: AsyncEngine, services: dict[str, str], directory: Path, config: LoadConfig
) -> dict:
    directory.mkdir(parents=True)
    config_file = directory / "config.json"
    raw = directory / "requests.json"
    write_json(config_file, asdict(config))
    done = asyncio.Event()
    error = None
    process_limit = (
        config.users / config.spawn_rate
        + config.warmup
        + config.duration
        + 2 * config.request_timeout
        + 25
    )
    with (directory / "locust.log").open("w", encoding="utf-8") as log:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "benchmarks.locustfile",
            "--config",
            str(config_file),
            "--output",
            str(raw),
            stdout=log,
            stderr=asyncio.subprocess.STDOUT,
        )
        watcher = asyncio.create_task(
            watch_resources(engine, services, process.pid, done)
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=process_limit)
        except TimeoutError:
            error = "Locust exceeded the subprocess time limit"
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            done.set()
            snapshots = await watcher
    write_json(directory / "resources.json", snapshots)

    if error or process.returncode != 0 or not raw.exists():
        report = {
            "status": "FAILED",
            "error": error or f"Locust exited with {process.returncode}",
            "config": asdict(config),
        }
    else:
        trial = json.loads(raw.read_text(encoding="utf-8"))
        samples = trial["samples"]
        elapsed = trial["elapsed_seconds"]
        operations = {}
        for scenario in select_scenarios(config.scenario):
            operations[scenario.name] = workload_metrics(
                [sample for sample in samples if sample["operation"] == scenario.name],
                trial["issued"].get(scenario.name, 0),
                elapsed,
            )
        report = {
            "status": "COMPLETED" if samples else "FAILED",
            "config": asdict(config),
            "elapsed_seconds": elapsed,
            "issuing_seconds": trial["issuing_seconds"],
            "stop_reason": trial["stop_reason"],
            "warmup_completed": trial["warmup_completed"],
            "warmup_failed": trial["warmup_failed"],
            "total": workload_metrics(samples, sum(trial["issued"].values()), elapsed),
            "operations": operations,
            "resources": resources_during_measurement(snapshots, trial),
        }
    write_json(directory / "report.json", report)
    return report


MEASUREMENT_COLUMNS = (
    "issued",
    "completed",
    "failed",
    "interrupted",
    "rps",
    "successful_rps",
    "bytes_per_second",
    "p50_ms",
    "p95_ms",
    "p99_ms",
    "mean_ms",
    "error_rate_pct",
)


def write_comparisons(directory: Path, reports: list[tuple[str, int, dict]]) -> None:
    rows = []
    grouped = defaultdict(list)
    for profile, repetition, report in reports:
        run = {
            "profile": profile,
            "users": report["config"]["users"],
            "repetition": repetition,
            "variant": report["config"]["variant"],
            "status": report["status"],
        }
        if report["status"] != "COMPLETED":
            rows.append(
                {
                    **run,
                    "operation": "all",
                    "error": report.get("error", "No completed samples"),
                }
            )
            continue
        for operation, metrics in {
            "all": report["total"],
            **report["operations"],
        }.items():
            rows.append(
                {
                    **run,
                    "operation": operation,
                    **{name: metrics[name] for name in MEASUREMENT_COLUMNS},
                }
            )
            grouped[(profile, run["users"], run["variant"], operation)].append(metrics)

    summary = []
    for (profile, users, variant, operation), runs in grouped.items():
        medians = {}
        spreads = {}
        for metric in MEASUREMENT_COLUMNS:
            available = [item[metric] for item in runs if item[metric] is not None]
            medians[f"median_{metric}"] = (
                round(median(available), 3) if available else None
            )
            spreads[f"spread_{metric}"] = (
                round(max(available) - min(available), 3)
                if len(available) > 1
                else None
            )
        summary.append(
            {
                "profile": profile,
                "users": users,
                "variant": variant,
                "operation": operation,
                "completed_runs": len(runs),
                **medians,
                **spreads,
            }
        )

    for filename, dataset in (("comparison.csv", rows), ("summary.csv", summary)):
        if dataset:
            columns = list(dict.fromkeys(key for row in dataset for key in row))
            with (directory / filename).open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=columns, lineterminator="\n")
                writer.writeheader()
                writer.writerows(dataset)


def configurations(args: argparse.Namespace, profiles: dict) -> dict[str, LoadConfig]:
    levels = (
        {
            f"users-{count}": {"users": count, "spawn_rate": min(count, 25)}
            for count in args.users
        }
        if args.users is not None
        else {name: profiles[name] for name in (args.profiles or ["low"])}
    )
    return {
        name: LoadConfig(
            host=args.host,
            variant="baseline",
            scenario=args.scenario,
            users=level["users"],
            spawn_rate=args.spawn_rate
            if args.spawn_rate is not None
            else level["spawn_rate"],
            duration=args.duration,
            warmup=args.warmup,
            max_requests=args.max_requests,
            request_timeout=args.request_timeout,
        )
        for name, level in levels.items()
    }


async def run(
    args: argparse.Namespace, levels: dict[str, LoadConfig], output: Path
) -> bool:
    settings = Settings()
    if settings.postgres_host not in LOCAL_DATABASE_HOSTS:
        raise ValueError("Load tests require the local synthetic PostgreSQL database")
    api = runtime_settings(args.host)
    dataset = await get_dataset(settings)
    expected_counts = PROFILES[args.dataset]
    parameters = dataset["manifest"]["parameters"]
    if (
        tuple(parameters[name] for name in ("users", "orders", "events"))
        != expected_counts
        or api["postgres_db"] != dataset["name"]
    ):
        raise ValueError(
            "The requested dataset does not match the running API and its PostgreSQL manifest"
        )
    budget = await connection_budget(
        settings,
        PoolConfig(
            size=api["db_pool_size"],
            overflow=api["db_max_overflow"],
            workers=api["web_concurrency"],
        ),
    )
    counts = await verify_responses(next(iter(levels.values())))
    services = {
        name: require_command("docker", "compose", "ps", "-q", name)
        for name in SERVICES
    }
    output.mkdir(parents=True, exist_ok=False)
    write_json(
        output / "metadata.json",
        {
            "started_at_utc": datetime.now(UTC).isoformat(),
            "git_commit": command_output("git", "rev-parse", "HEAD"),
            "git_worktree_dirty": bool(command_output("git", "status", "--porcelain")),
            "python": platform.python_version(),
            "host": platform.platform(),
            "host_cpu_count": psutil.cpu_count(),
            "host_memory_bytes": psutil.virtual_memory().total,
            "locust": version("locust"),
            "docker": docker_metadata(),
            "dataset_profile": args.dataset,
            "dataset": dataset,
            "api_settings": api,
            "postgres_budget": budget,
            "redis_mode": "disabled",
            "repetitions": args.repetitions,
            "think_time_seconds": 0.1,
            "levels": {name: asdict(config) for name, config in levels.items()},
            "preflight_matched_rows": counts,
        },
    )
    engine = create_engine(settings)
    reports = []
    try:
        for level, basis in levels.items():
            for repeat in range(1, args.repetitions + 1):
                for variant in VARIANTS if repeat % 2 else reversed(VARIANTS):
                    await wait_for_idle(
                        engine,
                        (api["db_statement_timeout_ms"] / 1000)
                        + api["db_pool_timeout"]
                        + 10,
                    )
                    directory = output / level / f"run-{repeat:02d}" / variant
                    config = LoadConfig(**{**asdict(basis), "variant": variant})
                    print(
                        f"{level} run {repeat}/{args.repetitions} {variant}", flush=True
                    )
                    report = await run_trial(engine, services, directory, config)
                    reports.append((level, repeat, report))
                    write_comparisons(output, reports)
                    if report["status"] == "COMPLETED":
                        print(
                            f"{report['total']['rps']:.2f} RPS, {report['total']['error_rate_pct']:.2f}% errors",
                            flush=True,
                        )
                    else:
                        print(
                            f"Trial failed; see {directory / 'report.json'}", flush=True
                        )
        await wait_for_idle(
            engine,
            (api["db_statement_timeout_ms"] / 1000) + api["db_pool_timeout"] + 10,
        )
    finally:
        await engine.dispose()
    if runtime_settings(args.host) != api:
        raise ValueError("The running API configuration changed during the benchmark")
    return all(report["status"] == "COMPLETED" for _, _, report in reports)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare local Baseline and Optimized APIs under bounded Locust load."
    )
    with PROFILE_CONFIG.open("rb") as file:
        profiles = tomllib.load(file)
    parser.add_argument(
        "--scenario",
        choices=["all", *(scenario.name for scenario in SCENARIOS)],
        default="all",
    )
    parser.add_argument("--dataset", choices=PROFILES, default="medium")
    parser.add_argument("--host", default="http://127.0.0.1:8008")
    levels = parser.add_mutually_exclusive_group()
    levels.add_argument("--profiles", nargs="+", choices=profiles)
    levels.add_argument("--users", nargs="+", type=int)
    parser.add_argument("--spawn-rate", type=float)
    parser.add_argument("--duration", type=float, default=15)
    parser.add_argument("--warmup", type=float, default=2)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--max-requests", type=int, default=2_000)
    parser.add_argument("--request-timeout", type=float, default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 10:
        parser.error("Use between 1 and 10 repetitions")
    output = args.output or Path("benchmarks/results") / datetime.now(UTC).strftime(
        "locust_%Y%m%dT%H%M%SZ"
    )
    if output.exists():
        parser.error(f"{output} already exists")
    try:
        completed = asyncio.run(run(args, configurations(args, profiles), output))
    except (
        OSError,
        TimeoutError,
        SQLAlchemyError,
        ValueError,
        httpx.HTTPError,
    ) as error:
        parser.exit(1, f"{error}\n")
    print(output)
    return 0 if completed else 1


if __name__ == "__main__":
    raise SystemExit(main())
