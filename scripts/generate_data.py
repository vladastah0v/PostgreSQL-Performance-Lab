import argparse
import asyncio
import json
import logging
from collections.abc import Iterable
from dataclasses import asdict
from enum import StrEnum
from itertools import batched
from time import perf_counter

import asyncpg

from app.core.config import Settings
from scripts.data_rows import (
    DatasetSpec,
    Row,
    category_rows,
    event_rows,
    order_rows,
    product_rows,
    user_rows,
)

logger = logging.getLogger(__name__)

TABLE_COLUMNS = {
    "categories": ("id", "name", "slug", "created_at"),
    "users": (
        "id",
        "email",
        "full_name",
        "city",
        "country",
        "status",
        "created_at",
        "updated_at",
    ),
    "products": (
        "id",
        "category_id",
        "name",
        "sku",
        "price",
        "is_active",
        "created_at",
    ),
    "orders": ("id", "user_id", "status", "total_amount", "created_at", "updated_at"),
    "order_items": ("id", "order_id", "product_id", "quantity", "unit_price"),
    "payments": ("id", "order_id", "status", "amount", "paid_at", "created_at"),
    "order_status_history": (
        "id",
        "order_id",
        "old_status",
        "new_status",
        "changed_at",
    ),
    "user_events": ("id", "user_id", "event_type", "payload", "created_at"),
}
TABLE_LIST = ", ".join(f"public.{table}" for table in TABLE_COLUMNS)
COUNT_QUERY = " UNION ALL ".join(
    f"SELECT '{table}' AS table_name, count(*) AS row_count FROM public.{table}"
    for table in TABLE_COLUMNS
)
PROFILES = {
    "small": (1_000, 10_000, 50_000),
    "medium": (10_000, 100_000, 500_000),
    "large": (100_000, 1_000_000, 5_000_000),
}
GENERATOR_VERSION = 1
PROGRESS_INTERVAL = 100_000
LOCAL_DATABASE_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "postgres"})


class LoadStrategy(StrEnum):
    COPY = "copy"
    INSERT = "insert"


class DatasetError(RuntimeError):
    pass


class BatchWriter:
    def __init__(
        self, connection: asyncpg.Connection, strategy: LoadStrategy, batch_size: int
    ):
        if batch_size < 1:
            raise ValueError("batch size must be positive")
        self.connection = connection
        self.strategy = strategy
        self.batch_size = batch_size
        self.counts = dict.fromkeys(TABLE_COLUMNS, 0)

    async def write(self, table: str, rows: Iterable[Row]) -> None:
        columns = TABLE_COLUMNS[table]
        placeholders = ", ".join(f"${index}" for index in range(1, len(columns) + 1))
        insert = (
            f"INSERT INTO public.{table} ({', '.join(columns)}) VALUES ({placeholders})"
        )
        for batch in batched(rows, self.batch_size):
            if self.strategy is LoadStrategy.COPY:
                await self.connection.copy_records_to_table(
                    table, schema_name="public", columns=columns, records=batch
                )
            else:
                await self.connection.executemany(insert, batch)

            before = self.counts[table]
            self.counts[table] += len(batch)
            if before // PROGRESS_INTERVAL != self.counts[table] // PROGRESS_INTERVAL:
                logger.info("Staged %d rows in %s", self.counts[table], table)


async def populate(writer: BatchWriter, spec: DatasetSpec) -> None:
    for table, rows in (
        ("categories", category_rows()),
        ("users", user_rows(spec)),
        ("products", product_rows(spec)),
    ):
        await writer.write(table, rows)
        logger.info("Staged %d rows in %s", writer.counts[table], table)

    for batch in batched(order_rows(spec), writer.batch_size):
        await writer.write("orders", (record.order for record in batch))
        await writer.write(
            "order_items", (item for record in batch for item in record.items)
        )
        await writer.write("payments", (record.payment for record in batch))
        await writer.write(
            "order_status_history",
            (entry for record in batch for entry in record.history),
        )
    for table in ("orders", "order_items", "payments", "order_status_history"):
        logger.info("Staged %d rows in %s", writer.counts[table], table)

    await writer.write("user_events", event_rows(spec))
    logger.info("Staged %d rows in user_events", writer.counts["user_events"])


async def acquire_dataset_locks(
    connection: asyncpg.Connection, *, clearing: bool = False
) -> None:
    await connection.execute("SET LOCAL lock_timeout = '5s'")
    acquired = await connection.fetchval(
        "SELECT pg_try_advisory_xact_lock(hashtext('performance_lab.dataset_generation'))"
    )
    if not acquired:
        raise DatasetError("A dataset operation is already running in this database")
    lock_mode = "ACCESS EXCLUSIVE" if clearing else "SHARE ROW EXCLUSIVE"
    await connection.execute(f"LOCK TABLE {TABLE_LIST} IN {lock_mode} MODE")


async def row_counts(connection: asyncpg.Connection) -> dict[str, int]:
    return {
        row["table_name"]: row["row_count"]
        for row in await connection.fetch(COUNT_QUERY)
    }


async def read_manifest(connection: asyncpg.Connection) -> dict | None:
    if not await connection.fetchval(
        "SELECT to_regclass('lab_data.dataset_manifest') IS NOT NULL"
    ):
        return None
    document = await connection.fetchval(
        "SELECT document FROM lab_data.dataset_manifest"
    )
    return json.loads(document) if document is not None else None


async def write_manifest(connection: asyncpg.Connection, manifest: dict) -> None:
    await connection.execute(
        """
        CREATE SCHEMA IF NOT EXISTS lab_data;
        CREATE TABLE IF NOT EXISTS lab_data.dataset_manifest (
            singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
            document jsonb NOT NULL CHECK (jsonb_typeof(document) = 'object')
        );
        """
    )
    await connection.execute(
        "INSERT INTO lab_data.dataset_manifest (document) VALUES ($1::jsonb)",
        json.dumps(manifest),
    )


def verify_counts(
    spec: DatasetSpec, expected: dict[str, int], actual: dict[str, int]
) -> None:
    if expected != actual:
        raise DatasetError(
            f"Row counts differ from generated counts. Expected {expected}, found {actual}"
        )
    if any(actual[table] != count for table, count in spec.fixed_counts.items()):
        raise DatasetError("Row counts differ from the dataset specification")
    if not 2 * spec.orders <= actual["order_items"] <= 4 * spec.orders:
        raise DatasetError("Order item count is outside the expected range")


async def generate_dataset(
    connection: asyncpg.Connection,
    spec: DatasetSpec,
    strategy: LoadStrategy,
    batch_size: int,
) -> dict:
    parameters = asdict(spec)
    async with connection.transaction():
        await acquire_dataset_locks(connection)
        manifest = await read_manifest(connection)
        existing = await row_counts(connection)
        if manifest is not None:
            if (
                manifest.get("version") != GENERATOR_VERSION
                or manifest.get("parameters") != parameters
            ):
                raise DatasetError(
                    "A different generated dataset already exists. Use an empty database or explicitly clear it"
                )
            verify_counts(spec, manifest["counts"], existing)
            return {"action": "reused", **manifest}
        if any(existing.values()):
            raise DatasetError(
                "The target tables contain data without a generation manifest"
            )

        writer = BatchWriter(connection, strategy, batch_size)
        load_started = perf_counter()
        await populate(writer, spec)
        load_seconds = perf_counter() - load_started
        actual = await row_counts(connection)
        verify_counts(spec, writer.counts, actual)
        for table, count in actual.items():
            await connection.execute(
                f"ALTER TABLE public.{table} ALTER COLUMN id RESTART WITH {count + 1}"
            )
        await connection.execute(f"ANALYZE {TABLE_LIST}")
        manifest = {
            "version": GENERATOR_VERSION,
            "parameters": parameters,
            "counts": actual,
        }
        await write_manifest(connection, manifest)

    return {"action": "generated", **manifest, "load_seconds": round(load_seconds, 6)}


def validate_clear_confirmation(settings: Settings, confirmation: str | None) -> None:
    if settings.postgres_host not in LOCAL_DATABASE_HOSTS:
        raise DatasetError(
            "Clearing is only available for a local or Compose PostgreSQL host"
        )
    if confirmation != settings.postgres_db:
        raise DatasetError("Pass --confirm-clear with the exact database name")


async def clear_dataset(connection: asyncpg.Connection, confirmation: str) -> dict:
    async with connection.transaction():
        if await connection.fetchval("SELECT current_database()") != confirmation:
            raise DatasetError("The connected database does not match the confirmation")
        await acquire_dataset_locks(connection, clearing=True)
        manifest = await read_manifest(connection)
        if manifest is None:
            raise DatasetError("The database has no generation manifest")
        if await row_counts(connection) != manifest.get("counts"):
            raise DatasetError("The row counts differ from the generation manifest")

        tables = ", ".join(f"ONLY public.{table}" for table in TABLE_COLUMNS)
        await connection.execute(f"TRUNCATE TABLE {tables} RESTART IDENTITY RESTRICT")
        await connection.execute("DELETE FROM lab_data.dataset_manifest")
    return {"action": "cleared", "counts": dict.fromkeys(TABLE_COLUMNS, 0)}


async def run(args: argparse.Namespace) -> dict:
    settings = Settings()
    users, orders, events = PROFILES[args.profile]
    spec = DatasetSpec(
        users if args.users is None else args.users,
        orders if args.orders is None else args.orders,
        events if args.events is None else args.events,
        args.seed,
    )
    if args.batch_size < 1:
        raise ValueError("batch size must be positive")
    if args.clear:
        validate_clear_confirmation(settings, args.confirm_clear)

    started = perf_counter()
    connection = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        timeout=10,
        server_settings={
            "timezone": "UTC",
            "application_name": "performance_lab_generator",
        },
    )
    try:
        if args.clear:
            result = await clear_dataset(connection, settings.postgres_db)
        else:
            logger.info(
                "Preparing %s using %s with batch size %d",
                spec,
                args.strategy,
                args.batch_size,
            )
            result = await generate_dataset(
                connection, spec, args.strategy, args.batch_size
            )
            result.update(strategy=args.strategy, batch_size=args.batch_size)
    finally:
        await connection.close()
    result.update(
        database=settings.postgres_db,
        elapsed_seconds=round(perf_counter() - started, 6),
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate or clear synthetic PostgreSQL lab data."
    )
    parser.add_argument("--profile", choices=PROFILES, default="small")
    parser.add_argument("--users", type=int, help="Override the profile's user count")
    parser.add_argument("--orders", type=int, help="Override the profile's order count")
    parser.add_argument("--events", type=int, help="Override the profile's event count")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--batch-size", type=int, default=5_000)
    parser.add_argument(
        "--strategy",
        type=LoadStrategy,
        choices=tuple(LoadStrategy),
        default=LoadStrategy.COPY,
    )
    parser.add_argument(
        "--clear", action="store_true", help="Clear the generated dataset and exit"
    )
    parser.add_argument(
        "--confirm-clear",
        metavar="DATABASE",
        help="Confirm clearing by exact database name",
    )
    args = parser.parse_args()
    if args.confirm_clear is not None and not args.clear:
        parser.error("--confirm-clear requires --clear")
    return args


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    try:
        result = asyncio.run(run(parse_args()))
    except (DatasetError, ValueError, OSError, asyncpg.PostgresError) as error:
        logger.error("%s", error)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
