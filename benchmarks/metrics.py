import math
from collections import Counter
from datetime import datetime, timedelta
from statistics import mean, median


def workload_metrics(samples: list[dict], issued: int, elapsed: float) -> dict:
    completed = len(samples)
    if completed > issued or elapsed <= 0:
        raise ValueError("Request samples must belong to a valid measurement window")
    interrupted = issued - completed
    failures = sum(sample["error"] is not None for sample in samples)
    times = sorted(sample["latency_ms"] for sample in samples)
    return {
        "issued": issued,
        "completed": completed,
        "failed": failures,
        "interrupted": interrupted,
        "rps": round(completed / elapsed, 3),
        "successful_rps": round((completed - failures) / elapsed, 3),
        "bytes_per_second": round(
            sum(sample["response_bytes"] for sample in samples) / elapsed, 3
        ),
        "error_rate_pct": round(100 * (failures + interrupted) / issued, 3)
        if issued
        else None,
        "p50_ms": round(median(times), 3) if times else None,
        "p95_ms": times[math.ceil(len(times) * 0.95) - 1] if times else None,
        "p99_ms": times[math.ceil(len(times) * 0.99) - 1] if times else None,
        "mean_ms": round(mean(times), 3) if times else None,
        "status_counts": dict(Counter(str(sample["status"]) for sample in samples)),
        "errors": dict(
            Counter(
                sample["error"] for sample in samples if sample["error"] is not None
            )
        ),
    }


def docker_bytes(value: str) -> int:
    # Docker prints binary prefixes for container memory and may print decimal ones for small values.
    units = {
        "B": 1,
        "kB": 10**3,
        "MB": 10**6,
        "GB": 10**9,
        "TB": 10**12,
        "KiB": 2**10,
        "MiB": 2**20,
        "GiB": 2**30,
        "TiB": 2**40,
    }
    amount = value.rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
    return round(float(amount) * units[value[len(amount) :]])


def resources_during_measurement(snapshots: list[dict], trial: dict) -> dict:
    start = datetime.fromisoformat(trial["started_at_utc"])
    end = start + timedelta(seconds=trial["elapsed_seconds"])
    measured = [
        item
        for item in snapshots
        if start <= datetime.fromisoformat(item["sampled_at_utc"]) <= end
    ]
    containers = {}
    for service in ("api", "postgres", "redis"):
        rows = [
            item["containers"][service]
            for item in measured
            if service in item["containers"]
        ]
        if rows:
            containers[service] = {
                "samples": len(rows),
                "mean_cpu_pct": round(mean(row["cpu_pct"] for row in rows), 2),
                "peak_cpu_pct": max(row["cpu_pct"] for row in rows),
                "peak_memory_bytes": max(row["memory_bytes"] for row in rows),
                "memory_limit_bytes": rows[0]["memory_limit_bytes"],
            }
    connections = [
        item["postgres_connections"]
        for item in measured
        if item["postgres_connections"] is not None
    ]
    generator = [item["locust"] for item in measured if item["locust"] is not None]
    return {
        "samples": len(measured),
        "containers": containers,
        "postgres_connections": {
            "samples": len(connections),
            "peak_total": max(row["total"] for row in connections),
            "peak_active": max(row["active"] for row in connections),
        }
        if connections
        else None,
        "locust": {
            "samples": len(generator),
            "peak_cpu_pct": max(row["cpu_pct"] for row in generator),
            "peak_memory_bytes": max(row["memory_bytes"] for row in generator),
        }
        if generator
        else None,
    }
