import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.config import Settings
from app.core.database import create_engine
from app.models import OrderStatus, UserStatus
from app.repositories.baseline.queries import BaselineRepository
from app.schemas.parameters import (
    DateRange,
    EventSearch,
    OrderSearch,
    SalesQuery,
    TopProductsQuery,
    UserSearch,
)
from app.services.baseline import BaselineService

type Operation = Callable[[BaselineService], Awaitable[object]]


def sample_operations(user_id: int, order_id: int) -> dict[str, Operation]:
    start, end = date(2025, 1, 1), date(2025, 12, 31)
    return {
        "users/search": lambda service: service.search_users(
            UserSearch(name="Alex", city="New York", status=UserStatus.ACTIVE)
        ),
        f"users/{user_id}/orders": lambda service: service.user_orders(
            user_id, OrderSearch()
        ),
        "orders": lambda service: service.orders(
            OrderSearch(
                status=OrderStatus.DELIVERED, date_from=start, date_to=end, offset=8_000
            )
        ),
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


async def explain_operation(connection: AsyncConnection, operation: Operation) -> dict:
    parameters_by_statement: dict[str, list[Any]] = {}

    def capture(
        connection, cursor, statement, parameters, context, executemany
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT "):
            parameters_by_statement.setdefault(statement, []).append(parameters)

    event.listen(connection.sync_connection, "before_cursor_execute", capture)
    try:
        async with AsyncSession(bind=connection) as session:
            await operation(BaselineService(BaselineRepository(session)))
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", capture)

    statements = []
    for sql, executions in parameters_by_statement.items():
        # The SQL and bound parameters come from the repository's own read-only SELECTs.
        plan = (
            await connection.exec_driver_sql(
                f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}", executions[0]
            )
        ).scalar_one()
        statements.append(
            {
                "sql": sql,
                "parameters": executions[0],
                "executions": len(executions),
                "explain": json.loads(plan) if isinstance(plan, str) else plan,
            }
        )
    return {
        "select_count": sum(len(values) for values in parameters_by_statement.values()),
        "statements": statements,
    }


async def collect_plans(user_id: int, order_id: int) -> dict:
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
            dataset = None
            if await connection.scalar(
                text("SELECT to_regclass('lab_data.dataset_manifest') IS NOT NULL")
            ):
                dataset = await connection.scalar(
                    text("SELECT document FROM lab_data.dataset_manifest")
                )
            operations = {
                name: await explain_operation(connection, operation)
                for name, operation in sample_operations(user_id, order_id).items()
            }
    finally:
        await engine.dispose()
    return {
        "measured_at_utc": datetime.now(UTC).isoformat(),
        "database": database,
        "dataset": dataset,
        "operations": operations,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Capture EXPLAIN plans for the baseline repository queries."
    )
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--order-id", type=int, default=1)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("experiments/sql/baseline/initial-plans.json"),
    )
    args = parser.parse_args()
    result = asyncio.run(collect_plans(args.user_id, args.order_id))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(args.output)


if __name__ == "__main__":
    main()
