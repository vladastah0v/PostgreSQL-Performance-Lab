import argparse
from pathlib import Path
from shutil import rmtree
from tempfile import TemporaryDirectory

from benchmarks.report_output import write_csv, write_json, write_markdown
from benchmarks.reporting import collect


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Собрать отчёт из сохранённых измерений."
    )
    parser.add_argument("--sql-run", type=Path)
    parser.add_argument("--fastapi-run", type=Path)
    parser.add_argument("--redis-run", type=Path)
    parser.add_argument("--locust-run", type=Path)
    parser.add_argument("--index-file", type=Path)
    parser.add_argument(
        "--format", choices=("all", "json", "csv", "markdown", "png"), default="all"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sources = {
        section: path.resolve()
        for section, path in (
            ("sql", args.sql_run),
            ("fastapi", args.fastapi_run),
            ("redis", args.redis_run),
            ("locust", args.locust_run),
            ("indexes", args.index_file),
        )
        if path is not None
    }
    for section, path in sources.items():
        if not (path.is_file() if section == "indexes" else path.is_dir()):
            parser.error(f"Источник недоступен: {path}")
    output = args.output.resolve()
    if output.exists() or args.output.is_symlink():
        parser.error(f"Каталог уже существует: {args.output}")
    try:
        comparisons = collect(sources)
        output.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".report-", dir=output.parent) as staging:
            temporary = Path(staging)
            charts = {}
            if args.format in ("all", "png"):
                from benchmarks.report_charts import write_charts

                charts = write_charts(temporary, comparisons)
            if args.format in ("all", "markdown"):
                write_markdown(
                    temporary / "report.md", comparisons, sources, charts, output
                )
            if args.format in ("all", "json"):
                write_json(
                    temporary / "report.json", comparisons, sources, charts, output
                )
            if args.format in ("all", "csv"):
                write_csv(temporary / "report.csv", comparisons, output)
            output.mkdir()
            try:
                for artifact in temporary.iterdir():
                    artifact.rename(output / artifact.name)
            except OSError:
                rmtree(output)
                raise
    except (OSError, TypeError, ValueError, KeyError) as error:
        parser.exit(1, f"Не удалось сформировать отчёт. {error}\n")
    print(f"{args.output} · записей {len(comparisons)} · графиков {len(charts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
