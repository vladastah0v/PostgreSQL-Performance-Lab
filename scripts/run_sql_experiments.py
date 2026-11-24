import argparse
import asyncio
import csv
import json
import platform
import subprocess
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from statistics import median
from time import perf_counter

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import Settings
from app.core.database import create_engine
from scripts.explain_queries import capture_selects, explain_select
from scripts.generate_data import COUNT_QUERY, LOCAL_DATABASE_HOSTS
from scripts.sql_experiments import Experiment, Variant, experiments


def command_output(*command: str) -> str | None:
    try:
        return subprocess.check_output(
            command, text=True, stderr=subprocess.DEVNULL, timeout=5
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def docker_metadata() -> dict | None:
    value = command_output(
        "docker", "info", "--format", "{{.ServerVersion}} {{.NCPU}} {{.MemTotal}}"
    )
    if not value:
        return None
    version, cpus, memory_bytes = value.split()
    return {
        "server_version": version,
        "cpus": int(cpus),
        "memory_bytes": int(memory_bytes),
    }


async def database_metadata(connection: AsyncConnection) -> dict:
    async with connection.begin():
        database = await connection.scalar(text("SELECT current_database()"))
        revision = await connection.scalar(
            text("SELECT version_num FROM public.alembic_version")
        )
        if revision != "0001":
            raise ValueError(
                f"Use a separate lab database at revision 0001. {database} is at {revision}."
            )

        extra_indexes = (
            (
                await connection.execute(
                    text("""
            SELECT index_class.relname
            FROM pg_index AS ix
            JOIN pg_class AS table_class ON table_class.oid = ix.indrelid
            JOIN pg_class AS index_class ON index_class.oid = ix.indexrelid
            JOIN pg_namespace AS ns ON ns.oid = table_class.relnamespace
            WHERE ns.nspname = 'public'
              AND NOT EXISTS (SELECT 1 FROM pg_constraint AS c WHERE c.conindid = ix.indexrelid)
        """)
                )
            )
            .scalars()
            .all()
        )
        if extra_indexes:
            raise ValueError(
                f"The experiment database has additional indexes: {', '.join(extra_indexes)}"
            )

        has_manifest = await connection.scalar(
            text("SELECT to_regclass('lab_data.dataset_manifest') IS NOT NULL")
        )
        dataset = (
            await connection.scalar(
                text("SELECT document FROM lab_data.dataset_manifest")
            )
            if has_manifest
            else None
        )
        counts = {
            name: count for name, count in await connection.exec_driver_sql(COUNT_QUERY)
        }
        if dataset is None or dataset.get("counts") != counts:
            raise ValueError(
                "Load the synthetic dataset with scripts.generate_data before running experiments"
            )

        settings = {
            name: await connection.scalar(
                text("SELECT current_setting(:name)"), {"name": name}
            )
            for name in (
                "server_version",
                "TimeZone",
                "shared_buffers",
                "work_mem",
                "effective_cache_size",
                "random_page_cost",
                "max_parallel_workers_per_gather",
                "jit",
                "statement_timeout",
            )
        }
    return {
        "database": database,
        "revision": revision,
        "settings": settings,
        "dataset": dataset,
    }


def plan_nodes(node: dict):
    yield node
    for child in node.get("Plans", []):
        yield from plan_nodes(child)


def trial_metrics(plans: list[dict]) -> dict:
    roots = [plan["Plan"] for plan in plans]
    return {
        "sql_count": len(plans),
        "execution_ms": round(sum(plan["Execution Time"] for plan in plans), 6),
        "planning_ms": round(sum(plan.get("Planning Time", 0) for plan in plans), 6),
        "shared_hit_blocks": sum(root.get("Shared Hit Blocks", 0) for root in roots),
        "shared_read_blocks": sum(root.get("Shared Read Blocks", 0) for root in roots),
        "reported_heap_fetches": sum(
            node.get("Heap Fetches", 0) for root in roots for node in plan_nodes(root)
        ),
    }


async def measure_variant(
    connection: AsyncConnection, variant: Variant, repetitions: int
) -> tuple[dict, list[dict], list | dict]:
    transaction = await connection.begin()
    try:
        if variant.index is None:
            await connection.exec_driver_sql(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
        else:
            await connection.exec_driver_sql(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
            )
        await connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")

        index = None
        if variant.index is not None:
            started = perf_counter()
            await connection.exec_driver_sql(variant.index.sql)
            index = {
                "name": variant.index.name,
                "sql": variant.index.sql,
                "build_wall_ms": round((perf_counter() - started) * 1_000, 3),
                "size_bytes": await connection.scalar(
                    text("SELECT pg_relation_size(CAST(:name AS regclass))"),
                    {"name": f"public.{variant.index.name}"},
                ),
            }

        read = await variant.prepare(connection)
        with capture_selects(connection) as statements:
            rows = await read(connection)
        row_count = len(rows["items"]) if isinstance(rows, dict) else len(rows)
        if not statements or row_count == 0:
            raise ValueError(
                "The experiment requires a nonempty result and at least one SELECT"
            )

        first_plans = []
        runs = []
        for repetition in range(1, repetitions + 1):
            documents = []
            for sql, parameters in statements:
                plan = await explain_select(connection, sql, parameters)
                documents.extend(plan)
                if repetition == 1:
                    first_plans.append(
                        {"sql": sql, "parameters": parameters, "explain": plan}
                    )
            runs.append({"repetition": repetition, **trial_metrics(documents)})
    finally:
        await transaction.rollback()

    times = [run["execution_ms"] for run in runs]
    return (
        {
            "row_count": row_count,
            "result_sha256": sha256(
                json.dumps(rows, sort_keys=True, default=str).encode()
            ).hexdigest(),
            "runs": runs,
            "median_ms": median(times),
            "min_ms": min(times),
            "max_ms": max(times),
            "index": index,
        },
        first_plans,
        rows,
    )


def write_json(path: Path, data) -> None:
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def plan_summary(entries: list[dict]) -> str:
    descr = set()
    for entry in entries:
        for document in entry["explain"]:
            for node in plan_nodes(document["Plan"]):
                target = node.get("Index Name", node.get("Relation Name"))
                descr.add(
                    f"{node['Node Type']} ({target})" if target else node["Node Type"]
                )
    return ", ".join(sorted(descr))


async def run_experiment(
    connection: AsyncConnection, output: Path, case: Experiment, repetitions: int
) -> dict:
    directory = output / case.name
    directory.mkdir()
    variants = {}
    statements = {}
    results = {}
    for label, variant in (("before", case.before), ("after", case.after)):
        metrics, plans, rows = await measure_variant(connection, variant, repetitions)
        variants[label], statements[label], results[label] = metrics, plans, rows
        write_json(
            directory / ("baseline.json" if label == "before" else "optimized.json"),
            metrics,
        )
        write_json(directory / f"explain_{label}.json", plans)

    if results["before"] != results["after"]:
        raise ValueError(
            "The before and after queries returned different rows or order"
        )
    before, after = variants["before"], variants["after"]
    duration_delta = before["median_ms"] - after["median_ms"]
    summary = {
        "experiment": case.name,
        "status": "PASSED",
        "rows": before["row_count"],
        "before_median_ms": before["median_ms"],
        "after_median_ms": after["median_ms"],
        "delta_ms": round(duration_delta, 6),
        "before_range_ms": f"{before['min_ms']}–{before['max_ms']}",
        "after_range_ms": f"{after['min_ms']}–{after['max_ms']}",
        "reduction_pct": round(100 * duration_delta / before["median_ms"], 2)
        if before["median_ms"]
        else None,
        "speedup": round(before["median_ms"] / after["median_ms"], 3)
        if after["median_ms"]
        else None,
        "before_selects": before["runs"][0]["sql_count"],
        "after_selects": after["runs"][0]["sql_count"],
    }
    report = (
        f"# {case.title}\n\n{case.change}\n\n"
        f"Порядок и значения строк совпали. Число строк в результате — {summary['rows']}. "
        f"Медиана SQL-времени за {repetitions} повторов составила {summary['before_median_ms']:.3f} мс "
        f"до изменения и {summary['after_median_ms']:.3f} мс после. "
        f"Число `SELECT` на операцию до изменения — {summary['before_selects']}, после — {summary['after_selects']}.\n\n"
        f"В [плане до изменения](explain_before.json) наблюдались {plan_summary(statements['before'])}.\n\n"
        f"В [плане после изменения](explain_after.json) наблюдались {plan_summary(statements['after'])}.\n"
    )
    (directory / "summary.md").write_text(report, encoding="utf-8")
    return summary


def write_comparison(output: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(field for row in rows for field in row))
    with (output / "comparison.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


async def run(
    args: argparse.Namespace, output: Path, cases: tuple[Experiment, ...]
) -> bool:
    settings = Settings(postgres_db=args.database)
    if settings.postgres_host not in LOCAL_DATABASE_HOSTS:
        raise ValueError(
            "SQL experiments require a local or Compose PostgreSQL instance"
        )
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            database = await database_metadata(connection)
            output.mkdir(parents=True, exist_ok=False)
            write_json(
                output / "metadata.json",
                {
                    "started_at_utc": datetime.now(UTC).isoformat(),
                    "git_commit": command_output("git", "rev-parse", "HEAD"),
                    "git_worktree_dirty": bool(
                        command_output("git", "status", "--porcelain")
                    ),
                    "host": platform.platform(),
                    "python": platform.python_version(),
                    "docker": docker_metadata(),
                    "database": database,
                    "parameters": {
                        "user_id": args.user_id,
                        "offset": args.offset,
                        "repetitions": args.repetitions,
                    },
                    "cache": "one warmup operation before each variant, shared buffers and OS cache left intact",
                    "measurement": "sum of PostgreSQL Execution Time from EXPLAIN ANALYZE per operation",
                },
            )
            comparisons = []
            for case in cases:
                print(f"Running {case.name}", flush=True)
                try:
                    comparisons.append(
                        await run_experiment(connection, output, case, args.repetitions)
                    )
                except (ValueError, SQLAlchemyError) as error:
                    detail = str(error)
                    comparisons.append(
                        {"experiment": case.name, "status": "FAILED", "error": detail}
                    )
                    (output / case.name / "summary.md").write_text(
                        f"# {case.title}\n\nFAILED\n\n{detail}\n", encoding="utf-8"
                    )
                write_comparison(output, comparisons)
            await database_metadata(connection)
    finally:
        await engine.dispose()
    return all(result["status"] == "PASSED" for result in comparisons)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run reproducible PostgreSQL SQL experiments on a synthetic lab database."
    )
    names = [case.name for case in experiments(1, 8_000)]
    parser.add_argument("--database", required=True)
    parser.add_argument("--scenario", choices=["all", *names], default="all")
    parser.add_argument(
        "--repetitions", type=int, choices=range(1, 21), default=5, metavar="1..20"
    )
    parser.add_argument("--offset", type=int, default=8_000)
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.offset <= 2**31 - 1 or not 1 <= args.user_id <= 2**63 - 1:
        parser.error(
            "--offset and --user-id must be positive and fit PostgreSQL integer and bigint respectively"
        )
    output = args.output or Path("benchmarks/results") / datetime.now(UTC).strftime(
        "sql_%Y%m%dT%H%M%SZ"
    )
    cases = tuple(
        case
        for case in experiments(args.user_id, args.offset)
        if args.scenario in ("all", case.name)
    )
    try:
        passed = asyncio.run(run(args, output, cases))
    except (ValueError, OSError, SQLAlchemyError) as error:
        parser.exit(1, f"{error}\n")
    print(output)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
