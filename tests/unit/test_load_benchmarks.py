from dataclasses import replace

import pytest

from benchmarks.metrics import (
    docker_bytes,
    resources_during_measurement,
    workload_metrics,
)
from benchmarks.scenarios.analytics import LoadConfig, select_scenarios


def test_load_metrics_keep_timeouts_and_interrupted_requests():
    samples = [
        {"latency_ms": 1.0, "status": 200, "response_bytes": 100, "error": None},
        {"latency_ms": 2.0, "status": 200, "response_bytes": 200, "error": None},
        {"latency_ms": 10.0, "status": 0, "response_bytes": 0, "error": "ReadTimeout"},
    ]
    report = workload_metrics(samples, issued=4, elapsed=2)
    assert report["completed"] == 3
    assert report["interrupted"] == report["failed"] == 1
    assert report["rps"] == 1.5
    assert report["successful_rps"] == 1
    assert report["error_rate_pct"] == 50
    assert report["bytes_per_second"] == 150
    assert [report[key] for key in ("p50_ms", "p95_ms", "p99_ms")] == [2, 10, 10]


def test_empty_load_scenario_has_unavailable_percentiles():
    report = workload_metrics([], issued=0, elapsed=3)
    assert report["p50_ms"] is None
    assert report["error_rate_pct"] is None


@pytest.mark.parametrize(
    ("value", "expected"), [("65.5MiB", 68_681_728), ("2GB", 2_000_000_000), ("0B", 0)]
)
def test_docker_memory_units(value, expected):
    assert docker_bytes(value) == expected


def test_resource_summary_uses_only_the_measured_window():
    def sample(instant, cpu, connections):
        return {
            "sampled_at_utc": f"2026-11-24T15:00:{instant:02d}+00:00",
            "containers": {
                "api": {"cpu_pct": cpu, "memory_bytes": 100, "memory_limit_bytes": 200}
            },
            "locust": None,
            "postgres_connections": {"total": connections, "active": connections - 1},
        }

    snapshots = [
        sample(1, 90, 9),
        sample(2, 20, 2),
        sample(3, 40, 3),
        sample(8, 95, 11),
    ]
    trial = {"started_at_utc": "2026-11-24T15:00:02+00:00", "elapsed_seconds": 5}
    resources = resources_during_measurement(snapshots, trial)
    assert resources["samples"] == 2
    assert resources["containers"]["api"]["mean_cpu_pct"] == 30
    assert resources["postgres_connections"]["peak_total"] == 3


@pytest.mark.parametrize(
    "override",
    [
        {"host": "http://example.test"},
        {"users": 201},
        {"duration": float("inf")},
        {"max_requests": 20_001},
        {"spawn_rate": 0.1},
    ],
)
def test_load_run_bounds_are_checked(override):
    default = LoadConfig(
        "http://127.0.0.1:8008", "all", "baseline", 10, 10, 2, 10, 1_000, 10
    )
    with pytest.raises(ValueError):
        replace(default, **override)


def test_both_variants_share_parameters_and_fixed_operation_names():
    scenarios = select_scenarios("all")
    assert len({scenario.name for scenario in scenarios}) == len(scenarios) == 8
    for scenario in scenarios:
        assert scenario.url("baseline").replace(
            "/baseline/", "/optimized/"
        ) == scenario.url("optimized")
    [sales] = select_scenarios("sales")
    assert sales.cache_status("baseline") is None
    assert sales.cache_status("optimized") == "BYPASS"
