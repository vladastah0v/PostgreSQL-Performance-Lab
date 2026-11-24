from collections.abc import Sequence
from pathlib import Path

from matplotlib.axes import Axes
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator

from benchmarks.reporting import (
    CACHE_MODES,
    CACHE_OPERATIONS,
    INDEX_SELECTS,
    PERCENTILES,
    Comparison,
    Number,
)

COLORS = ("#345b87", "#13836f", "#af672d")


def style(axis: Axes, title: str, xlabel: str, ylabel: str, *, grid: str = "y") -> None:
    axis.set(title=title, xlabel=xlabel, ylabel=ylabel)
    axis.set_axisbelow(True)
    axis.grid(axis=grid, color="#dce4eb")
    axis.spines[["right", "top"]].set_visible(False)


def grouped_bars(
    axis: Axes,
    labels: Sequence[str],
    series: dict[str, list[Number | None]],
) -> None:
    width = 0.8 / len(series)
    for number, (label, values) in enumerate(series.items()):
        offset = (number - (len(series) - 1) / 2) * width
        present = [
            (i + offset, value) for i, value in enumerate(values) if value is not None
        ]
        if present:
            positions, heights = zip(*present, strict=True)
            axis.bar(
                positions,
                heights,
                width,
                label=label,
                color=COLORS[number % len(COLORS)],
            )
    axis.set_xticks(range(len(labels)), labels)
    axis.margins(y=0.2)
    axis.set_ylim(bottom=0)
    axis.legend(loc="upper left", frameon=False)


def durations(
    axis: Axes, items: list[Comparison], title: str, *, log: bool = False
) -> None:
    before = [item.before[item.metric] for item in items]
    after = [item.after[item.metric] for item in items]
    positions = list(range(len(items)))
    axis.hlines(positions, before, after, color="#a3b3c3", linewidth=2)
    axis.scatter(
        before, positions, color=COLORS[0], label=items[0].variants[0], zorder=3
    )
    axis.scatter(
        after,
        positions,
        color=COLORS[1],
        label=items[0].variants[1],
        marker="D",
        zorder=3,
    )
    axis.set_yticks(positions, [item.title for item in items])
    axis.set_ylim(len(items) - 0.5, -0.5)
    if log and min(before + after) > 0:
        axis.set_xscale("log")
    else:
        axis.set_xlim(left=0)
    units = (
        "Медиана, мс (логарифмическая шкала)"
        if axis.get_xscale() == "log"
        else "Медиана, мс"
    )
    style(axis, title, units, "", grid="x")
    axis.legend(loc="best", frameon=False)


def load_curve(
    axis: Axes, items: list[Comparison], metric: str, label: str, title: str
) -> None:
    for number, side in enumerate(("before", "after")):
        observations = [
            (item.users, getattr(item, side))
            for item in items
            if metric in getattr(item, side)
        ]
        if observations:
            users, values = zip(*observations, strict=True)
            center = [value[metric] for value in values]
            below = [
                value[metric] - value.get(f"min_{metric}", value[metric])
                for value in values
            ]
            above = [
                value.get(f"max_{metric}", value[metric]) - value[metric]
                for value in values
            ]
            axis.errorbar(
                users,
                center,
                yerr=[below, above],
                marker=("o", "D")[number],
                color=COLORS[number],
                label=items[0].variants[number],
                capsize=4,
                linewidth=2,
            )
    style(axis, title, "Одновременные пользователи", label)
    axis.set_xticks(sorted({item.users for item in items}))
    axis.set_ylim(bottom=0)
    axis.legend(loc="best", frameon=False)


def write_charts(directory: Path, items: list[Comparison]) -> dict[str, str]:
    complete = [item for item in items if item.status == "COMPLETED"]
    sql = [item for item in complete if item.section == "sql"]
    fastapi = [
        item
        for item in complete
        if item.section == "fastapi"
        and all(p in side for p in PERCENTILES for side in (item.before, item.after))
        and item.operation in {"history", "top-products", "pagination"}
    ]
    load = [
        item
        for item in complete
        if item.section == "locust" and item.operation == "all"
    ]
    redis = [item for item in complete if item.section == "redis"]
    reads = [item for item in sql if item.operation in INDEX_SELECTS]
    writes = [item for item in complete if item.section == "indexes"]
    generated = {}

    def save(figure: Figure, filename: str, title: str) -> None:
        FigureCanvasAgg(figure)
        figure.savefig(directory / filename, dpi=150, facecolor="white")
        generated[filename] = title

    if sql:
        figure = Figure(figsize=(10, 5), layout="constrained")
        durations(figure.subplots(), sql, "Время выполнения SQL до и после", log=True)
        save(figure, "sql-execution.png", "Время выполнения SQL до и после")

    if fastapi:
        figure = Figure(figsize=(3.9 * len(fastapi), 4), layout="constrained")
        for axis, item in zip(
            figure.subplots(1, len(fastapi), squeeze=False)[0], fastapi, strict=True
        ):
            grouped_bars(
                axis,
                ("p50", "p95", "p99"),
                {
                    label: [point[metric] for metric in PERCENTILES]
                    for label, point in zip(
                        item.variants, (item.before, item.after), strict=True
                    )
                },
            )
            style(axis, item.title, "Перцентиль", "HTTP-задержка, мс")
            axis.yaxis.set_major_locator(MaxNLocator(5))
        figure.suptitle("Задержка FastAPI по операциям")
        save(figure, "api-latency.png", "Задержка API p50, p95 и p99")

    for metric, unit, filename, title in (
        (
            "rps",
            "Запросов в секунду",
            "rps-by-concurrency.png",
            "Пропускная способность Locust",
        ),
        ("error_rate_pct", "Ошибки, %", "error-rate.png", "Доля ошибок Locust"),
    ):
        if load and any(
            metric in side for item in load for side in (item.before, item.after)
        ):
            figure = Figure(figsize=(8, 4.8), layout="constrained")
            axis = figure.subplots()
            load_curve(axis, load, metric, unit, title)
            axis.set_title(f"{title}\nМедиана и диапазон повторов")
            save(figure, filename, title)

    if redis:
        measurements = {}
        for item in redis:
            operation, mode = item.operation.split("/")
            measurements[operation, "disabled"] = item.before["p50_ms"]
            measurements[operation, mode] = item.after["p50_ms"]
        operations = [
            name for name in CACHE_OPERATIONS if (name, "disabled") in measurements
        ]
        series = {
            title: [measurements.get((name, mode)) for name in operations]
            for mode, title in CACHE_MODES.items()
        }
        figure = Figure(figsize=(9, 4.8), layout="constrained")
        axis = figure.subplots()
        grouped_bars(axis, operations, series)
        style(axis, "Влияние Redis-кэша на HTTP-задержку", "Операция", "p50, мс")
        save(figure, "redis-cache.png", "Холодный и прогретый Redis-кэш")

    if reads or writes:
        groups = [
            (items, title, log)
            for items, title, log in (
                (reads, "Чтение · индексные эксперименты SELECT", True),
                (writes, "Запись · ревизии 0001 и 0002", False),
            )
            if items
        ]
        figure = Figure(figsize=(8, len(groups) * 2.8), layout="constrained")
        for axis, (comparison, title, log) in zip(
            figure.subplots(len(groups), 1, squeeze=False)[:, 0], groups, strict=True
        ):
            durations(axis, comparison, title, log=log)
        figure.suptitle("Влияние индексов на чтение и запись")
        save(figure, "index-impact.png", "Влияние индексов на SELECT и INSERT/UPDATE")
    return generated
