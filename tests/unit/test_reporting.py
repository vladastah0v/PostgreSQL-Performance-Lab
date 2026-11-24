import csv
import json

import pytest

from benchmarks.report_charts import write_charts
from benchmarks.report_output import write_csv, write_json, write_markdown
from benchmarks.reporting import Comparison, collect, latency_changes, locust_side


@pytest.mark.parametrize(
    ("before", "after", "delta", "reduction", "speedup"),
    [
        (12, 4, 8, 66.666667, 3),
        (4, 8, -4, -100, 0.5),
        (4, 0, 4, 100, None),
        (0, 4, -4, None, 0),
        (None, 4, None, None, None),
    ],
)
def test_latency_changes(before, after, delta, reduction, speedup):
    assert latency_changes(before, after) == {
        "delta_ms": delta,
        "reduction_pct": reduction,
        "speedup": speedup,
    }


def test_missing_input_has_no_measurements_or_charts(tmp_path):
    items = collect({})
    assert len(items) == 5
    assert all(
        item.status == "NOT_RUN" and not item.before and not item.after
        for item in items
    )
    assert write_charts(tmp_path, items) == {}
    assert not list(tmp_path.iterdir())


def test_failed_sql_run_does_not_export_timings(tmp_path):
    (tmp_path / "comparison.csv").write_text(
        "experiment,status,error\nmissing-index,FAILED,aborted\n", encoding="utf-8"
    )
    case = tmp_path / "missing-index"
    case.mkdir()
    for name in ("baseline.json", "optimized.json"):
        (case / name).write_text('{"median_ms": 12}', encoding="utf-8")
    items = collect({"sql": tmp_path})
    [failed] = [item for item in items if item.status == "FAILED"]
    assert failed.operation == "missing-index"
    assert not failed.before and not failed.after
    assert "aborted" in failed.change
    assert all(item.status == "NOT_RUN" for item in items if item is not failed)


def test_locust_keeps_counts_when_an_operation_has_no_completed_requests():
    side = locust_side(
        [
            {
                "status": "COMPLETED",
                "issued": "1",
                "completed": "0",
                "failed": "0",
                "interrupted": "1",
                "rps": "0",
                "error_rate_pct": "100",
            }
        ],
        [],
        1,
    )
    assert side["completed"] == 0
    assert side["interrupted"] == side["failed_runs"] == 1
    assert side["error_rate_pct"] == 100
    assert "p95_ms" not in side


def test_json_csv_and_markdown_preserve_regression_and_plan_path(tmp_path):
    plans = tuple(tmp_path / f"plan {mode}.json" for mode in ("before", "after"))
    for plan in plans:
        plan.write_text("[]", encoding="utf-8")
    output = tmp_path / "report"
    output.mkdir()
    items = [
        Comparison(
            "sql",
            "case",
            "SELECT",
            "median_ms",
            ("До", "После"),
            {"median_ms": 2, "sql_count": 1},
            {"median_ms": 3, "sql_count": 1},
            plans=plans,
        )
    ]
    write_json(output / "report.json", items, {}, {}, output)
    write_csv(output / "report.csv", items, output)
    write_markdown(output / "report.md", items, {}, {}, output)
    data = json.loads((output / "report.json").read_text(encoding="utf-8"))
    [result] = data["comparisons"]
    assert result["latency_changes"]["reduction_pct"] == -50
    assert (output / result["plans"]["after"]).exists()
    with (output / "report.csv").open(encoding="utf-8") as file:
        [latency, query_count] = csv.DictReader(file)
    assert latency["delta_ms"] == "-1"
    assert query_count["metric"] == "sql_count"
    assert query_count["speedup"] == ""
    assert "[До](../plan%20before.json)" in (output / "report.md").read_text(
        encoding="utf-8"
    )
