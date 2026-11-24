import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from statistics import median
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.config import Settings
from app.core.database import create_engine
from app.models import OrderStatus, UserStatus
from app.repositories.baseline.queries import BaselineRepository
from app.repositories.optimized.queries import OptimizedRepository
from app.schemas.cursors import encode_cursor
from app.schemas.parameters import (
    CursorOrderSearch,
    DateRange,
    EventSearch,
    OrderSearch,
    SalesQuery,
    TopProductsQuery,
    UserSearch,
)
from app.services.commerce import CommerceService

type Operation = Callable[[CommerceService], Awaitable[object]]

REPOSITORIES = {"baseline": BaselineRepository, "optimized": OptimizedRepository}


def orders_sample(offset: int = 8_000, limit: int = 20) -> OrderSearch:
    return OrderSearch(
        status=OrderStatus.DELIVERED,
        date_from=date(2025, 1, 1),
        date_to=date(2025, 12, 31),
        offset=offset,
        limit=limit,
    )


def sample_operations(user_id: int, order_id: int) -> dict[str, Operation]:
    start, end = date(2025, 1, 1), date(2025, 12, 31)
    return {
        "users/search": lambda service: service.search_users(
            UserSearch(name="Alex", city="New York", status=UserStatus.ACTIVE)
        ),
        f"users/{user_id}/orders": lambda service: service.user_orders(
            user_id, OrderSearch()
        ),
        "orders": lambda service: service.orders(orders_sample()),
        f"orders/{order_id}/details": lambda service: service.order_details(order_id),
        "analytics/sales": lambda service: service.sales(
            SalesQuery(period="month", date_from=start, date_to=end)
        ),
        "analytics/top-products": lambda service: service.top_products(
            TopProductsQuery(date_from=start, date_to=end)
        ),
        "analytics/customers": lambda service: service.customers(
            DateRange(date_from=start, date_to=end)
        ),
        "events": lambda service: service.events(
            EventSearch(user_id=user_id, event_type="product_view")
        ),
    }


@contextmanager
def capture_selects(connection: AsyncConnection):
    statements = []

    def capture(
        connection, cursor, statement, parameters, context, executemany
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT "):
            statements.append((statement, parameters))

    event.listen(connection.sync_connection, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", capture)


async def explain_select(
    connection: AsyncConnection, sql: str, parameters
) -> list[dict]:
    plan = (
        await connection.exec_driver_sql(
            f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}", parameters
        )
    ).scalar_one()
    return json.loads(plan) if isinstance(plan, str) else plan


async def explain_operation(
    connection: AsyncConnection, operation: Operation, implementation: str
) -> dict:
    parameters_by_statement: dict[str, list[Any]] = {}
    with capture_selects(connection) as captured:
        async with AsyncSession(bind=connection) as session:
            await operation(CommerceService(REPOSITORIES[implementation](session)))
    for statement, parameters in captured:
        parameters_by_statement.setdefault(statement, []).append(parameters)

    statements = []
    for sql, executions in parameters_by_statement.items():
        # The SQL and bound parameters come from the repository's own read-only SELECTs.
        statements.append(
            {
                "sql": sql,
                "parameters": executions[0],
                "executions": len(executions),
                "explain": await explain_select(connection, sql, executions[0]),
            }
        )
    return {
        "select_count": sum(len(values) for values in parameters_by_statement.values()),
        "statements": statements,
    }


async def compare_pagination(connection: AsyncConnection, offset: int) -> dict:
    offset_query = orders_sample(offset=offset)
    async with AsyncSession(bind=connection) as session:
        service = CommerceService(OptimizedRepository(session))
        preceding = await service.orders(orders_sample(offset=offset - 1, limit=1))
        if not preceding.items:
            raise ValueError("The dataset does not contain the requested offset")
        anchor = preceding.items[0]
        cursor_query = CursorOrderSearch(
            **offset_query.model_dump(exclude={"offset"}),
            cursor=encode_cursor(offset_query, "orders", anchor.id, anchor.created_at),
        )
        offset_page = await service.orders(offset_query)
        cursor_page = await service.orders(cursor_query)
    if (
        not offset_page.items
        or offset_page.items != cursor_page.items
        or offset_page.has_more != cursor_page.has_more
    ):
        raise ValueError("Offset and keyset did not return the same nonempty page")

    queries = {"offset": offset_query, "keyset": cursor_query}
    runs = {name: [] for name in queries}
    plans = {}
    for iteration in range(5):
        for name in list(queries) if iteration % 2 == 0 else reversed(queries):
            query = queries[name]
            captured = await explain_operation(
                connection, lambda service: service.orders(query), "optimized"
            )
            entry = captured["statements"][0]
            runs[name].append(entry["explain"][0]["Execution Time"])
            if iteration == 0:
                plans[name] = entry
    return {
        "query": offset_query.model_dump(mode="json"),
        "row_ids": [order.id for order in offset_page.items],
        "anchor": {"id": anchor.id, "created_at": anchor.created_at.isoformat()},
        "execution_time_ms": runs,
        "median_ms": {name: median(times) for name, times in runs.items()},
        "plans": plans,
    }


async def collect_plans(
    user_id: int,
    order_id: int,
    implementation: str = "baseline",
    pagination_offset: int | None = None,
) -> dict:
    engine = create_engine(Settings())
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            database = {
                setting: await connection.scalar(
                    text("SELECT current_setting(:setting)"), {"setting": setting}
                )
                for setting in (
                    "server_version",
                    "TimeZone",
                    "shared_buffers",
                    "work_mem",
                    "max_parallel_workers_per_gather",
                    "statement_timeout",
                )
            }
            database["alembic_revision"] = await connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            dataset = None
            if await connection.scalar(
                text("SELECT to_regclass('lab_data.dataset_manifest') IS NOT NULL")
            ):
                dataset = await connection.scalar(
                    text("SELECT document FROM lab_data.dataset_manifest")
                )
            operations = {
                name: await explain_operation(connection, operation, implementation)
                for name, operation in sample_operations(user_id, order_id).items()
            }
            if implementation == "optimized":
                operations[
                    "users/search (city and rare status)"
                ] = await explain_operation(
                    connection,
                    lambda service: service.search_users(
                        UserSearch(city="Osaka", status=UserStatus.BLOCKED)
                    ),
                    implementation,
                )
                operations["orders (latest without status)"] = await explain_operation(
                    connection,
                    lambda service: service.orders(OrderSearch()),
                    implementation,
                )
            pagination = (
                await compare_pagination(connection, pagination_offset)
                if pagination_offset is not None
                else None
            )
    finally:
        await engine.dispose()
    return {
        "measured_at_utc": datetime.now(UTC).isoformat(),
        "database": database,
        "implementation": implementation,
        "dataset": dataset,
        "operations": operations,
        **({"pagination": pagination} if pagination is not None else {}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Capture EXPLAIN plans for the baseline or optimized repository queries."
    )
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--order-id", type=int, default=1)
    parser.add_argument("--implementation", choices=REPOSITORIES, default="baseline")
    parser.add_argument(
        "--pagination-offset",
        type=int,
        help="Compare offset and keyset on the same optimized order page",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.pagination_offset is not None and (
        args.implementation != "optimized"
        or not 1 <= args.pagination_offset <= 2**31 - 1
    ):
        parser.error(
            "--pagination-offset requires --implementation optimized and a positive offset"
        )
    output = args.output or Path(
        f"experiments/sql/{args.implementation}/initial-plans.json"
    )
    result = asyncio.run(
        collect_plans(
            args.user_id, args.order_id, args.implementation, args.pagination_offset
        )
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(output)


if __name__ == "__main__":
    main()
