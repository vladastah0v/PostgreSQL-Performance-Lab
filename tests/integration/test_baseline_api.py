from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import get_db_session
from app.main import create_app
from app.models import (
    Category,
    Order,
    OrderItem,
    OrderStatus,
    Payment,
    PaymentStatus,
    Product,
    User,
    UserEvent,
    UserStatus,
)

pytestmark = pytest.mark.asyncio
DAY = datetime(2035, 7, 14, tzinfo=UTC)
DAY_PARAMS = {"date_from": DAY.date().isoformat(), "date_to": DAY.date().isoformat()}


@dataclass
class Records:
    user: User
    other_user: User
    first_order: Order
    second_order: Order
    pending_order: Order
    product: Product
    other_product: Product
    events: list[UserEvent]
    token: str


def make_order(
    user: User, instant: datetime, status: OrderStatus, *items: tuple[Product, int]
) -> Order:
    total = sum((product.price * count for product, count in items), Decimal(0))
    paid = status in {OrderStatus.PAID, OrderStatus.DELIVERED}
    return Order(
        user=user,
        status=status,
        total_amount=total,
        created_at=instant,
        updated_at=instant,
        items=[
            OrderItem(product=product, quantity=count, unit_price=product.price)
            for product, count in items
        ],
        payments=[
            Payment(
                amount=total,
                status=PaymentStatus.PAID if paid else PaymentStatus.PENDING,
                paid_at=instant if paid else None,
                created_at=instant,
            )
        ],
    )


@pytest_asyncio.fixture
async def records(db_session: AsyncSession) -> Records:
    token = uuid4().hex
    user = User(
        email=f"first-{token}@example.test",
        full_name="Alex_% Test",
        city="New York",
        country="US",
    )
    other_user = User(
        email=f"second-{token}@example.test",
        full_name="AlexXY Test",
        city="New York",
        country="US",
        status=UserStatus.INACTIVE,
    )
    category = Category(name="API Category", slug=f"test-{token}")
    product = Product(
        name="Product A", sku=f"a-{token}", price=Decimal("12.50"), category=category
    )
    other_product = Product(
        name="Product B", sku=f"b-{token}", price=Decimal("5.00"), category=category
    )
    first = make_order(user, DAY, OrderStatus.PAID, (product, 2), (other_product, 1))
    second = make_order(user, DAY, OrderStatus.DELIVERED, (product, 3))
    pending = make_order(
        user, DAY + timedelta(hours=1), OrderStatus.PENDING, (other_product, 10)
    )
    tomorrow = make_order(
        other_user, DAY + timedelta(days=1), OrderStatus.PAID, (other_product, 2)
    )
    events = [
        UserEvent(
            user=user,
            event_type="product_view",
            payload={"device": "mobile"},
            created_at=DAY,
        ),
        UserEvent(
            user=user,
            event_type="product_view",
            payload={"device": "desktop"},
            created_at=DAY,
        ),
        UserEvent(
            user=user,
            event_type="search",
            payload={},
            created_at=DAY + timedelta(hours=1),
        ),
        UserEvent(
            user=other_user, event_type="product_view", payload={}, created_at=DAY
        ),
    ]
    db_session.add_all([first, second, pending, tomorrow, *events])
    await db_session.flush()
    db_session.expunge_all()
    return Records(
        user, other_user, first, second, pending, product, other_product, events, token
    )


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(lab_mode=True, lab_allow_docker_gateway=False))

    async def database() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db_session] = database
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://localhost/api/v1/baseline/",
    ) as client:
        yield client


async def test_user_search_filters_and_escapes_wildcards(
    client: httpx.AsyncClient, records: Records
) -> None:
    response = await client.get(
        "users/search",
        params={"name": "_%", "email": records.token.upper(), "city": "new YORK"},
    )
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [records.user.id]
    response = await client.get(
        "users/search", params={"status": "inactive", "email": records.token}
    )
    assert [item["id"] for item in response.json()["items"]] == [records.other_user.id]


async def test_user_orders_item_counts_and_n_plus_one(
    client: httpx.AsyncClient, db_session: AsyncSession, records: Records
) -> None:
    connection = await db_session.connection()
    statements = []

    def capture(
        connection, cursor, statement, parameters, context, executemany
    ) -> None:
        if statement.startswith("SELECT "):
            statements.append(statement)

    event.listen(connection.sync_connection, "before_cursor_execute", capture)
    try:
        response = await client.get(
            f"users/{records.user.id}/orders", params={"limit": 3}
        )
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", capture)
    assert response.status_code == 200
    assert {row["id"]: row["item_count"] for row in response.json()["items"]} == {
        records.first_order.id: 2,
        records.second_order.id: 1,
        records.pending_order.id: 1,
    }
    # One customer lookup, one order list and one line-item query per returned order.
    assert len(statements) == 5


async def test_orders_use_stable_offset_pagination_and_status_filter(
    client: httpx.AsyncClient, records: Records
) -> None:
    params = {**DAY_PARAMS, "limit": 2}
    response = await client.get("orders", params=params)
    assert response.status_code == 200
    data = response.json()
    same_time_ids = sorted(
        [records.first_order.id, records.second_order.id], reverse=True
    )
    assert [item["id"] for item in data["items"]] == [
        records.pending_order.id,
        same_time_ids[0],
    ]
    assert data["has_more"] is True
    response = await client.get("orders", params={**params, "offset": 2})
    assert [item["id"] for item in response.json()["items"]] == same_time_ids[1:]
    assert response.json()["has_more"] is False
    response = await client.get("orders", params={**DAY_PARAMS, "status": "paid"})
    assert [item["id"] for item in response.json()["items"]] == [records.first_order.id]


async def test_order_details_include_items_and_payments(
    client: httpx.AsyncClient, records: Records
) -> None:
    response = await client.get(f"orders/{records.first_order.id}/details")
    assert response.status_code == 200
    details = response.json()
    assert details["total_amount"] == "30.00"
    assert sorted(item["product_id"] for item in details["items"]) == sorted(
        [records.product.id, records.other_product.id]
    )
    [payment] = details["payments"]
    assert payment["status"] == "paid"
    assert payment["amount"] == "30.00"


@pytest.mark.parametrize(
    ("period", "bucket"), [("day", "2035-07-14"), ("month", "2035-07-01")]
)
async def test_sales_aggregates_only_qualifying_orders(
    client: httpx.AsyncClient, records: Records, period: str, bucket: str
) -> None:
    response = await client.get(
        "analytics/sales", params={**DAY_PARAMS, "period": period}
    )
    assert response.status_code == 200
    assert response.json() == [{"period": bucket, "order_count": 2, "revenue": "67.50"}]


async def test_top_products_are_ranked_by_units(
    client: httpx.AsyncClient, records: Records
) -> None:
    response = await client.get("analytics/top-products", params=DAY_PARAMS)
    assert response.status_code == 200
    assert response.json() == [
        {
            "product_id": records.product.id,
            "name": "Product A",
            "units_sold": 5,
            "revenue": "62.50",
        },
        {
            "product_id": records.other_product.id,
            "name": "Product B",
            "units_sold": 1,
            "revenue": "5.00",
        },
    ]


async def test_customer_stats_keep_customers_without_matching_orders(
    client: httpx.AsyncClient, db_session: AsyncSession, records: Records
) -> None:
    total_customers = await db_session.scalar(select(func.count()).select_from(User))
    response = await client.get("analytics/customers", params=DAY_PARAMS)
    assert response.status_code == 200
    assert response.json() == {
        "total_customers": total_customers,
        "customers_with_orders": 1,
        "repeat_customers": 1,
        "order_count": 2,
        "revenue": "67.50",
    }


async def test_event_filters_and_tie_break_order(
    client: httpx.AsyncClient, records: Records
) -> None:
    response = await client.get(
        "events",
        params={
            **DAY_PARAMS,
            "user_id": records.user.id,
            "event_type": "product_view",
            "limit": 1,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert [item["id"] for item in data["items"]] == [
        max(event.id for event in records.events[:2])
    ]
    assert data["has_more"] is True


@pytest.mark.parametrize(
    "path", ["users/9223372036854775807/orders", "orders/9223372036854775807/details"]
)
async def test_missing_entity_returns_404(client: httpx.AsyncClient, path: str) -> None:
    assert (await client.get(path)).status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "events",
        "users/search?limit=101",
        "orders?offset=-1",
        "orders?status=invalid",
        "orders?date_from=2035-07-15&date_to=2035-07-14",
        "orders/9223372036854775808/details",
    ],
)
async def test_invalid_filters_return_422(client: httpx.AsyncClient, path: str) -> None:
    assert (await client.get(path)).status_code == 422


OPTIMIZED = "http://localhost/api/v1/optimized/"


@pytest.mark.parametrize(
    "scenario",
    [
        "users",
        "orders",
        "max_date",
        "user_orders",
        "details",
        "daily_sales",
        "monthly_sales",
        "top_products",
        "customers",
        "events",
        "empty_customers",
        "missing_order",
        "missing_user",
    ],
)
async def test_optimized_offset_responses_match_baseline(
    client: httpx.AsyncClient, records: Records, scenario: str
) -> None:
    path, params = {
        "users": ("users/search", {"email": records.token, "limit": 1, "offset": 1}),
        "orders": ("orders", {**DAY_PARAMS, "limit": 1, "offset": 1}),
        "max_date": ("orders", {"date_to": "9999-12-31", "limit": 1}),
        "user_orders": (
            f"users/{records.user.id}/orders",
            {**DAY_PARAMS, "limit": 1, "offset": 1},
        ),
        "details": (f"orders/{records.first_order.id}/details", {}),
        "daily_sales": ("analytics/sales", DAY_PARAMS),
        "monthly_sales": ("analytics/sales", {**DAY_PARAMS, "period": "month"}),
        "top_products": ("analytics/top-products", {**DAY_PARAMS, "limit": 1}),
        "customers": ("analytics/customers", DAY_PARAMS),
        "events": (
            "events",
            {
                **DAY_PARAMS,
                "user_id": records.user.id,
                "event_type": "product_view",
                "limit": 1,
                "offset": 1,
            },
        ),
        "empty_customers": ("analytics/customers", {"date_from": "2036-01-01"}),
        "missing_order": ("orders/9223372036854775807/details", {}),
        "missing_user": ("users/9223372036854775807/orders", {}),
    }[scenario]
    baseline = await client.get(path, params=params)
    optimized = await client.get(OPTIMIZED + path, params=params)
    expected_status = 404 if scenario.startswith("missing_") else 200
    assert optimized.status_code == baseline.status_code == expected_status
    assert optimized.json() == baseline.json()
    assert "X-Next-Cursor" not in optimized.headers


@pytest.mark.parametrize("scenario", ["users", "orders", "user_orders", "events"])
async def test_keyset_pagination_returns_the_same_rows_in_the_same_order(
    client: httpx.AsyncClient, records: Records, scenario: str
) -> None:
    path, params = {
        "users": ("users/search", {"email": records.token}),
        "orders": ("orders", DAY_PARAMS),
        "user_orders": (f"users/{records.user.id}/orders", DAY_PARAMS),
        "events": (
            "events",
            {**DAY_PARAMS, "user_id": records.user.id, "event_type": "product_view"},
        ),
    }[scenario]
    baseline = await client.get(path, params=params)
    assert baseline.status_code == 200
    expected = baseline.json()["items"]
    assert len(expected) > 1

    cursor = "start"
    for index, item in enumerate(expected):
        response = await client.get(
            OPTIMIZED + path, params={**params, "limit": 1, "cursor": cursor}
        )
        assert response.status_code == 200
        page = response.json()
        assert page["items"] == [item]
        assert page["offset"] == 0
        assert page["has_more"] == (index < len(expected) - 1)
        assert ("X-Next-Cursor" in response.headers) == page["has_more"]
        cursor = response.headers.get("X-Next-Cursor")


async def test_cursor_rejects_invalid_tokens_and_changed_filters(
    client: httpx.AsyncClient, records: Records
) -> None:
    route = OPTIMIZED + "orders"
    first = await client.get(
        route, params={**DAY_PARAMS, "limit": 1, "cursor": "start"}
    )
    assert first.status_code == 200
    cursor = first.headers["X-Next-Cursor"]

    bad_queries = [
        {**DAY_PARAMS, "cursor": cursor, "status": "paid"},
        {**DAY_PARAMS, "cursor": "start", "offset": 1},
        {**DAY_PARAMS, "cursor": "not-a-cursor"},
    ]
    for params in bad_queries:
        assert (await client.get(route, params=params)).status_code == 422
    assert (
        await client.get(
            OPTIMIZED + f"users/{records.user.id}/orders",
            params={**DAY_PARAMS, "cursor": cursor},
        )
    ).status_code == 422


async def test_optimized_order_details_keep_empty_relationships(
    client: httpx.AsyncClient, db_session: AsyncSession, records: Records
) -> None:
    order = Order(
        user_id=records.user.id,
        total_amount=Decimal("0.00"),
        created_at=DAY,
        updated_at=DAY,
    )
    db_session.add(order)
    await db_session.flush()
    route = f"orders/{order.id}/details"
    db_session.expunge_all()
    baseline = await client.get(route)
    optimized = await client.get(OPTIMIZED + route)
    assert optimized.status_code == baseline.status_code == 200
    assert optimized.json() == baseline.json()
    assert optimized.json()["items"] == optimized.json()["payments"] == []


async def test_optimized_history_loads_all_item_counts_in_one_query(
    client: httpx.AsyncClient, db_session: AsyncSession, records: Records
) -> None:
    connection = await db_session.connection()
    statements = []

    def capture(
        connection, cursor, statement, parameters, context, executemany
    ) -> None:
        if statement.startswith("SELECT "):
            statements.append(statement)

    event.listen(connection.sync_connection, "before_cursor_execute", capture)
    try:
        response = await client.get(
            OPTIMIZED + f"users/{records.user.id}/orders", params={"limit": 3}
        )
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", capture)
    assert response.status_code == 200
    assert {row["id"]: row["item_count"] for row in response.json()["items"]} == {
        records.first_order.id: 2,
        records.second_order.id: 1,
        records.pending_order.id: 1,
    }
    assert len(statements) == 3
