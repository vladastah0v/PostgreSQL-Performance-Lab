import csv
import json
import os
from pathlib import Path
from urllib.parse import quote

from benchmarks.reporting import SECTIONS, Comparison, Number, latency_changes

DISPLAY_NAMES = {
    "median_ms": "Медиана SQL",
    "total_ms": "Медиана операции",
    "p50_ms": "p50 HTTP",
    "p95_ms": "p95 HTTP",
}
RESOURCE_DETAILS = {
    "shared_hit_blocks": ("Попадания в общие буферы", ""),
    "shared_read_blocks": ("Чтения общих блоков", ""),
    "size_bytes": ("Размер индекса", "байт"),
    "median_wal_bytes": ("WAL", "байт"),
    "api_mean_cpu_pct": ("CPU API", "%"),
    "postgres_mean_cpu_pct": ("CPU PostgreSQL", "%"),
    "api_peak_memory_bytes": ("Пик памяти API", "байт"),
    "postgres_peak_memory_bytes": ("Пик памяти PostgreSQL", "байт"),
    "db_connections_peak_total": ("Пик соединений PostgreSQL", ""),
    "pool_p50_ms": ("p50 получения соединения", "мс"),
    "response_bytes_p50": ("p50 размера ответа", "байт"),
}
HTTP_DETAILS = {
    "p50_ms": ("p50 HTTP", "мс"),
    "p95_ms": ("p95 HTTP", "мс"),
    "p99_ms": ("p99 HTTP", "мс"),
    "rps": ("Пропускная способность", "запр./с"),
    "error_rate_pct": ("Доля ошибок", "%"),
    "failed_runs": ("Неудачных прогонов", ""),
}


def reference(path: Path, output: Path) -> str:
    return Path(os.path.relpath(path, start=output)).as_posix()


def link(path: Path, output: Path, label: str | None = None) -> str:
    return f"[{label or path.name}]({quote(reference(path, output))})"


def file_reference(path: Path | None, output: Path) -> str | None:
    return reference(path, output) if path is not None else None


def item_name(item: Comparison) -> str:
    return f"{item.title} · {item.level}" if item.level else item.title


def formatted(value: Number | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.3f}".rstrip("0").rstrip(".").replace(",", " ").replace(".", ",")


def pair_value(item: Comparison, key: str) -> str:
    return f"{formatted(item.before.get(key))} → {formatted(item.after.get(key))}"


def cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def markdown_row(*values: str) -> str:
    return "| " + " | ".join(cell(value) for value in values) + " |"


def markdown_table(lines: list[str], headers: tuple[str, ...]) -> None:
    lines.extend(("", markdown_row(*headers), markdown_row(*("---" for _ in headers))))


def write_markdown(
    destination: Path,
    items: list[Comparison],
    inputs: dict[str, Path],
    charts: dict[str, str],
    output: Path,
) -> None:
    lines = ["# Сравнительный отчёт PostgreSQL Performance Lab", ""]
    lines.append(
        "Разность измеряется как время до минус время после. Снижение задержки "
        "равно разности, делённой на время до, в процентах. Ускорение показывает "
        "отношение времени до ко времени после. Отрицательная разность указывает на рост задержки."
    )
    lines.extend(
        (
            "",
            "Каждое сравнение использует свой исходный эксперимент. "
            "SQL-время, полная HTTP-задержка и варианты PostgreSQL и Redis описаны в соответствующих разделах. "
            "Прочерк означает отсутствие значения в источнике.",
        )
    )
    if charts:
        lines.extend(("", "## Графики", ""))
        lines.extend(f"- [{title}]({filename})" for filename, title in charts.items())
    for section, title in SECTIONS.items():
        lines.extend(("", f"## {title}"))
        if source := inputs.get(section):
            lines.extend(
                (
                    "",
                    f"Исходные файлы эксперимента. {link(source, output, 'Артефакты')}",
                )
            )
        rows = [item for item in items if item.section == section]
        markdown_table(
            lines,
            (
                "Операция",
                "Статус",
                "Варианты",
                "Метрика, мс",
                "До",
                "После",
                "Δ, мс",
                "Снижение, %",
                "Ускорение, ×",
                "SQL-запросы",
                "Планы",
            ),
        )
        for item in rows:
            before, after = item.before.get(item.metric), item.after.get(item.metric)
            changes = latency_changes(before, after)
            sql = pair_value(
                item, "sql_count" if section == "sql" else "sql_queries_p50"
            )
            plans = " · ".join(
                link(path, output, label)
                for path, label in zip(item.plans, ("До", "После"), strict=True)
                if path
            )
            lines.append(
                markdown_row(
                    item_name(item),
                    item.status,
                    " → ".join(item.variants),
                    DISPLAY_NAMES[item.metric],
                    formatted(before),
                    formatted(after),
                    *(formatted(value) for value in changes.values()),
                    sql,
                    plans or "—",
                )
            )
        relevant = [item for item in rows if item.status != "NOT_RUN"]
        if relevant:
            lines.extend(("", "### Изменения и источники", ""))
            for item in relevant:
                if section == "locust" and item.operation != "all":
                    continue
                links = " ".join(link(path, output) for path in item.sources)
                lines.append(f"- **{item_name(item)}.** {item.change} {links}".rstrip())
        details = []
        for item in relevant:
            if section == "locust" and item.operation != "all":
                continue
            for key, (label, unit) in (HTTP_DETAILS | RESOURCE_DETAILS).items():
                if key in item.before or key in item.after:
                    details.append(
                        (item_name(item), label, unit or "—", pair_value(item, key))
                    )
        if details:
            lines.extend(("", "### Метрики нагрузки и ресурсов"))
            markdown_table(lines, ("Операция", "Измерение", "Единица", "До → после"))
            lines.extend(markdown_row(*row) for row in details)
    lines.extend(
        (
            "",
            "Формат источников, команды и границы измерений описаны в документации экспериментов.",
            "",
        )
    )
    destination.write_text("\n".join(lines), encoding="utf-8")


def write_json(
    destination: Path,
    items: list[Comparison],
    inputs: dict[str, Path],
    charts: dict[str, str],
    output: Path,
) -> None:
    rows = [
        {
            "section": item.section,
            "operation": item.operation,
            "title": item.title,
            "status": item.status,
            "level": item.level or None,
            "users": item.users,
            "variants": item.variants,
            "latency_metric": item.metric,
            "before": item.before,
            "after": item.after,
            "latency_changes": latency_changes(
                item.before.get(item.metric), item.after.get(item.metric)
            ),
            "change": item.change,
            "sources": [reference(path, output) for path in item.sources],
            "plans": dict(
                zip(
                    ("before", "after"),
                    (file_reference(path, output) for path in item.plans),
                    strict=True,
                )
            ),
        }
        for item in items
    ]
    result = {
        "schema_version": 1,
        "inputs": {
            section: reference(path, output) for section, path in inputs.items()
        },
        "comparisons": rows,
        "charts": charts,
    }
    destination.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


CSV_COLUMNS = (
    "section",
    "operation",
    "title",
    "level",
    "users",
    "status",
    "before_variant",
    "after_variant",
    "metric",
    "before",
    "after",
    "delta_ms",
    "reduction_pct",
    "speedup",
    "change",
    "source",
    "before_plan",
    "after_plan",
)


def write_csv(destination: Path, items: list[Comparison], output: Path) -> None:
    with destination.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for item in items:
            for metric in dict.fromkeys((item.metric, *item.before, *item.after)):
                before, after = item.before.get(metric), item.after.get(metric)
                writer.writerow(
                    {
                        "section": item.section,
                        "operation": item.operation,
                        "title": item.title,
                        "level": item.level,
                        "users": item.users,
                        "status": item.status,
                        "before_variant": item.variants[0],
                        "after_variant": item.variants[1],
                        "metric": metric,
                        "before": before,
                        "after": after,
                        **(
                            latency_changes(before, after)
                            if metric.endswith("_ms")
                            else {}
                        ),
                        "change": item.change,
                        "source": reference(item.sources[0], output)
                        if item.sources
                        else None,
                        "before_plan": file_reference(item.plans[0], output),
                        "after_plan": file_reference(item.plans[1], output),
                    }
                )
