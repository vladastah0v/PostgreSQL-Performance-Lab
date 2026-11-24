from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from statistics import median

from scripts.sql_experiments import experiments

type Number = int | float
type Values = dict[str, Number]

SECTIONS = {
    "sql": "SQL-эксперименты",
    "fastapi": "Профилирование FastAPI",
    "redis": "Режимы Redis-кэша",
    "locust": "Нагрузка Locust",
    "indexes": "Стоимость записи с индексами",
}
PERCENTILES = ("p50_ms", "p95_ms", "p99_ms")
INDEX_SELECTS = (
    "missing-index",
    "composite-index",
    "partial-index",
    "covering-index",
    "jsonb",
)
CACHE_OPERATIONS = ("sales", "top-products", "customers")
CACHE_MODES = {"disabled": "Без кэша", "cold": "Холодный кэш", "warm": "Прогретый кэш"}


@dataclass(frozen=True)
class Comparison:
    section: str
    operation: str
    title: str
    metric: str
    variants: tuple[str, str]
    before: Values = field(default_factory=dict)
    after: Values = field(default_factory=dict)
    change: str = ""
    sources: tuple[Path, ...] = ()
    plans: tuple[Path | None, Path | None] = (None, None)
    level: str = ""
    users: int | None = None
    failed: bool = False
    single: bool = False

    @property
    def status(self) -> str:
        if self.metric in self.before and self.metric in self.after:
            return "COMPLETED"
        if self.single and self.metric in self.before:
            return "OBSERVED"
        if self.failed:
            return "FAILED"
        if self.before or self.after:
            return "INCOMPLETE"
        return "NOT_RUN"


def latency_changes(
    before: Number | None, after: Number | None
) -> dict[str, float | None]:
    if before is None or after is None:
        return dict(delta_ms=None, reduction_pct=None, speedup=None)
    delta = before - after
    return {
        "delta_ms": round(delta, 6),
        "reduction_pct": round(100 * delta / before, 6) if before else None,
        "speedup": round(before / after, 6) if after else None,
    }


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Ожидался JSON-объект в {path}")
    return data


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def measured(values: dict, keys) -> Values:
    selected = {}
    for key in keys:
        value = values.get(key)
        if value is None or value == "":
            continue
        number = float(value) if isinstance(value, str) else value
        if (
            isinstance(number, bool)
            or not isinstance(number, (int, float))
            or not isfinite(number)
            or number < 0
        ):
            raise ValueError(f"Недопустимое значение метрики {key}: {value!r}")
        selected[key] = number
    return selected


def source_files(*paths: Path) -> tuple[Path, ...]:
    return tuple(path for path in paths if path.is_file())


def sql_side(data: dict) -> Values:
    details = measured(data, ("median_ms", "min_ms", "max_ms", "row_count"))
    details.update(
        summarize_records(
            [
                measured(run, ("sql_count", "shared_hit_blocks", "shared_read_blocks"))
                for run in data.get("runs", [])
            ]
        )
    )
    if data.get("index"):
        details.update(measured(data["index"], ("size_bytes", "build_wall_ms")))
    return details


def sql_comparisons(root: Path) -> list[Comparison]:
    metadata = read_json(root / "metadata.json")
    parameters = metadata.get("parameters", {})
    index = root / "comparison.csv"
    statuses = {row["experiment"]: row for row in read_csv(index)}
    comparisons = []
    for case in experiments(
        parameters.get("user_id", 1), parameters.get("offset", 8_000)
    ):
        operation = root / case.name
        baseline, optimized = operation / "baseline.json", operation / "optimized.json"
        before, after = read_json(baseline), read_json(optimized)
        status = statuses.get(case.name, {}).get("status")
        hashes_match = before.get("result_sha256") == after.get("result_sha256")
        if status == "PASSED" and not hashes_match:
            raise ValueError(f"Не совпадают результаты SQL-эксперимента {case.name}")
        failed = status == "FAILED"
        detail = statuses.get(case.name, {}).get("error")
        explanation = case.change + (
            f" Эксперимент завершился ошибкой: {detail}" if failed and detail else ""
        )
        plans = tuple(
            path if path.is_file() else None
            for path in (
                operation / "explain_before.json",
                operation / "explain_after.json",
            )
        )
        comparisons.append(
            Comparison(
                "sql",
                case.name,
                case.title,
                "median_ms",
                ("До", "После"),
                sql_side(before) if not failed else {},
                sql_side(after) if not failed else {},
                explanation,
                source_files(baseline, optimized, operation / "summary.md", index),
                plans,
                failed=failed,
            )
        )
    return comparisons


def api_side(data: dict) -> Values:
    values = measured(data.get("http_success", {}), PERCENTILES + ("min_ms", "max_ms"))
    values.update(
        measured(
            data,
            (
                "sql_queries_p50",
                "transport_and_client_p50_ms",
                "response_bytes_p50",
                "success_count",
                "max_slots",
            ),
        )
    )
    for name, value in measured(
        data.get("phases_p50_ms", {}),
        ("sql", "pool", "serialization", "logic", "cache"),
    ).items():
        values[f"{name}_p50_ms"] = value
    statuses = data.get("status_counts", {})
    if total := sum(statuses.values()):
        values["error_rate_pct"] = 100 * (total - statuses.get("200", 0)) / total
        values["requests"] = total
    return values


def fastapi_comparisons(root: Path) -> list[Comparison]:
    latency, pool, orm, cache = (
        root / f"{name}.json" for name in ("latency", "pool", "orm", "cache")
    )
    operations = read_json(latency).get("results", {})
    descriptions = {
        "history": (
            "История заказов",
            "Пакетная загрузка связанных позиций с selectinload.",
        ),
        "top-products": (
            "Рейтинг товаров",
            "Агрегация и LIMIT выполняются до чтения названий.",
        ),
        "pagination": (
            "Пагинация заказов",
            "Keyset вместо OFFSET на той же странице. Курсор получен до замера.",
        ),
    }
    comparisons = []
    for name, (title, change) in descriptions.items():
        operation = operations.get(name, {})
        comparisons.append(
            Comparison(
                "fastapi",
                name,
                title,
                "p50_ms",
                ("OFFSET", "Keyset")
                if name == "pagination"
                else ("Baseline", "Optimized"),
                api_side(operation.get("before", {})),
                api_side(operation.get("after", {})),
                change,
                source_files(latency),
            )
        )

    configurations = read_json(pool).get("results", {}).get("configurations", {})
    control = api_side(configurations.get("control", {}))
    for name, title in {
        "larger-pool": "Два соединения в пуле",
        "overflow": "Одно соединение и overflow",
        "short-timeout": "Короткий таймаут пула",
        "two-workers": "Два воркера",
    }.items():
        config = configurations.get(name, {})
        settings = config.get("config", {})
        comparison = api_side(config)
        description = (
            f"Пул {settings['size']} + {settings['overflow']}, воркеров {settings['workers']}, таймаут {settings['timeout']} с. "
            "HTTP-квантили рассчитаны по успешным ответам. Доля ошибок включает все ответы."
            if settings
            else ""
        )
        comparisons.append(
            Comparison(
                "fastapi",
                f"pool/{name}",
                title,
                "p50_ms",
                ("Контроль", "Изменение"),
                control,
                comparison,
                description,
                source_files(pool),
            )
        )
    measurements = read_json(orm).get("results", {}).get("measurements", {})
    sides = [
        measured(
            measurements.get(name, {}).get("median_ms", {}),
            ("total_ms", "sql_ms", "application_ms"),
        )
        for name in ("orm", "core")
    ]
    comparisons.append(
        Comparison(
            "fastapi",
            "orm-core",
            "Материализация ORM и Core",
            "total_ms",
            ("ORM", "Core"),
            *sides,
            "Одинаковая SQL-выборка и модели ответа. Измерено время клиентской обработки строк и SQL.",
            source_files(orm),
        )
    )
    warm = api_side(read_json(cache).get("results", {}).get("warm", {}))
    comparisons.append(
        Comparison(
            "fastapi",
            "cache-hit",
            "Профиль попадания Redis",
            "p50_ms",
            ("HIT", "—"),
            warm,
            change="Одиночная серия HIT. Число SQL-запросов и HTTP-квантили сохранены для этой серии.",
            sources=source_files(cache),
            single=True,
        )
    )
    return comparisons


def redis_comparisons(root: Path) -> list[Comparison]:
    paths = {mode: root / f"{mode}.json" for mode in CACHE_MODES}
    documents = {mode: read_json(path) for mode, path in paths.items()}
    before = documents["disabled"]
    comparisons = []
    for operation in CACHE_OPERATIONS:
        left = before.get("operations", {}).get(operation, {})
        for mode, outcome in (("cold", "MISS"), ("warm", "HIT")):
            after = documents[mode]
            right = after.get("operations", {}).get(operation, {})
            if (
                left
                and right
                and (
                    before["dataset"] != after["dataset"]
                    or before["concurrency"] != after["concurrency"]
                    or left["parameters"] != right["parameters"]
                    or left["response_sha256"] != right["response_sha256"]
                )
            ):
                raise ValueError(f"Несопоставимые замеры Redis для {operation}/{mode}")
            change = (
                "Инвалидация меняла версию ключа перед каждым измерением."
                if mode == "cold"
                else "Кэш прогрет до начала измерений."
            )
            comparisons.append(
                Comparison(
                    "redis",
                    f"{operation}/{mode}",
                    f"{operation} · {outcome}",
                    "p50_ms",
                    (CACHE_MODES["disabled"], CACHE_MODES[mode]),
                    measured(left, PERCENTILES + ("min_ms", "max_ms")),
                    measured(right, PERCENTILES + ("min_ms", "max_ms")),
                    change,
                    source_files(paths["disabled"], paths[mode]),
                )
            )
    return comparisons


def summarize_records(records: list[Values]) -> Values:
    columns = defaultdict(list)
    for record in records:
        for name, value in record.items():
            columns[name].append(value)
    return {name: round(median(samples), 6) for name, samples in columns.items()}


def resource_values(report: dict) -> Values:
    result = {}
    resources = report.get("resources", {})
    for name in ("api", "postgres"):
        values = measured(
            resources.get("containers", {}).get(name, {}),
            ("mean_cpu_pct", "peak_memory_bytes"),
        )
        result.update({f"{name}_{key}": value for key, value in values.items()})
    connections = measured(
        resources.get("postgres_connections") or {}, ("peak_total", "peak_active")
    )
    result.update(
        {f"db_connections_{key}": value for key, value in connections.items()}
    )
    return result


def locust_side(
    rows: list[dict], resources: list[Values], failed_attempts: int
) -> Values:
    valid = [
        row for row in rows if row["status"] == "COMPLETED" and int(row["issued"]) > 0
    ]
    fields = PERCENTILES + (
        "rps",
        "successful_rps",
        "error_rate_pct",
        "bytes_per_second",
        "mean_ms",
    )
    used = [measured(row, fields) for row in valid]
    details = summarize_records(used)
    if details:
        details.update(summarize_records(resources))
        details["completed_runs"] = len(used)
        for metric in ("rps", "error_rate_pct"):
            values = [side[metric] for side in used if metric in side]
            if values:
                details[f"min_{metric}"], details[f"max_{metric}"] = (
                    min(values),
                    max(values),
                )
        for metric in ("issued", "completed", "failed", "interrupted"):
            details[metric] = sum(int(row[metric]) for row in valid)
    if failed_attempts:
        details["failed_runs"] = failed_attempts
    return details


def locust_comparisons(root: Path) -> list[Comparison]:
    metadata = read_json(root / "metadata.json")
    index = root / "comparison.csv"
    categories = defaultdict(list)
    for row in read_csv(index):
        categories[(row["profile"], row["operation"], row["variant"])].append(row)
    comparisons = []
    variants = ("baseline", "optimized")
    for level, config in sorted(
        metadata.get("levels", {}).items(), key=lambda item: (item[1]["users"], item[0])
    ):
        resource_data = defaultdict(list)
        reports = defaultdict(list)
        failed = {}
        for variant in variants:
            trials = categories[(level, "all", variant)]
            failed[variant] = sum(row["status"] == "FAILED" for row in trials)
            for row in trials:
                if row["status"] == "COMPLETED":
                    source = (
                        root
                        / level
                        / f"run-{int(row['repetition']):02d}"
                        / variant
                        / "report.json"
                    )
                    if source.is_file():
                        resource_data[variant].append(
                            resource_values(read_json(source))
                        )
                        reports[variant].append(source)
        for operation in ("all", *metadata.get("preflight_matched_rows", {})):
            sides = [
                locust_side(
                    categories[(level, operation, variant)],
                    resource_data[variant] if operation == "all" else [],
                    failed[variant],
                )
                for variant in variants
            ]
            comparisons.append(
                Comparison(
                    "locust",
                    operation,
                    "Все операции" if operation == "all" else operation,
                    "p95_ms",
                    ("Baseline", "Optimized"),
                    *sides,
                    "Медиана метрик отдельных прогонов. HTTP-ошибки входят в измерение.",
                    source_files(index, *reports["baseline"], *reports["optimized"])
                    if operation == "all"
                    else source_files(index),
                    level=level,
                    users=config["users"],
                    failed=any(failed.values()),
                )
            )
    return comparisons


def index_comparisons(path: Path) -> list[Comparison]:
    document = read_json(path)
    comparisons = []
    for operation in ("insert", "update"):
        sides = []
        for revision in ("0001", "0002"):
            values = document.get("write", {}).get(revision, {}).get(operation, {})
            detail = measured(values, ("median_ms", "median_wal_bytes"))
            detail.update(
                summarize_records(
                    [
                        measured(run, ("shared_hit_blocks", "shared_read_blocks"))
                        for run in values.get("runs", [])
                    ]
                )
            )
            sides.append(detail)
        notes = "Ревизия 0002 добавляет индексы Optimized API."
        if rows := document.get("rows_per_statement"):
            notes += (
                f" Измерены операции на {rows} строках с откатом после каждого прогона."
            )
        comparisons.append(
            Comparison(
                "indexes",
                operation,
                operation.upper(),
                "median_ms",
                ("0001", "0002"),
                *sides,
                notes,
                source_files(path),
            )
        )
    return comparisons


def collect(sources: dict[str, Path]) -> list[Comparison]:
    result = []
    for section, load in (
        ("sql", sql_comparisons),
        ("fastapi", fastapi_comparisons),
        ("redis", redis_comparisons),
        ("locust", locust_comparisons),
        ("indexes", index_comparisons),
    ):
        comparisons = load(sources[section]) if section in sources else []
        result.extend(
            comparisons
            or [
                Comparison(
                    section, "all", SECTIONS[section], "median_ms", ("До", "После")
                )
            ]
        )
    return result
