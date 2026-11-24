from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from scripts import run_sql_experiments
from scripts.sql_experiments import experiments


def test_metrics_sum_each_statement_without_double_counting_child_buffers():
    plans = [
        {
            "Execution Time": 3.1,
            "Planning Time": 0.3,
            "Plan": {
                "Shared Hit Blocks": 8,
                "Shared Read Blocks": 2,
                "Plans": [{"Shared Hit Blocks": 6, "Heap Fetches": 2}],
            },
        },
        {"Execution Time": 1.6, "Plan": {"Shared Hit Blocks": 3, "Heap Fetches": 1}},
    ]

    assert run_sql_experiments.trial_metrics(plans) == {
        "sql_count": 2,
        "execution_ms": 4.7,
        "planning_ms": 0.3,
        "shared_hit_blocks": 11,
        "shared_read_blocks": 2,
        "reported_heap_fetches": 3,
    }


@pytest.mark.asyncio
async def test_experiment_rejects_different_row_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    case = experiments(1, 8_000)[0]
    measure = AsyncMock(
        side_effect=[({}, [], [{"id": 1}, {"id": 2}]), ({}, [], [{"id": 2}, {"id": 1}])]
    )
    monkeypatch.setattr(run_sql_experiments, "measure_variant", measure)

    with pytest.raises(ValueError, match="different rows or order"):
        await run_sql_experiments.run_experiment(None, tmp_path, case, 5)

    directory = tmp_path / case.name
    assert (directory / "explain_before.json").is_file()
    assert (directory / "explain_after.json").is_file()
    assert not (directory / "summary.md").exists()
