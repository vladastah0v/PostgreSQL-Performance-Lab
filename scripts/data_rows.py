import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from random import Random
from typing import Any

from app.models import OrderStatus, PaymentStatus, UserStatus

type Row = tuple[Any, ...]

CATEGORY_NAMES = (
    "Books",
    "Electronics",
    "Computers",
    "Phones",
    "Home",
    "Garden",
    "Furniture",
    "Kitchen",
    "Clothing",
    "Shoes",
    "Sports",
    "Outdoors",
    "Beauty",
    "Health",
    "Toys",
    "Games",
    "Pets",
    "Office",
    "Automotive",
    "Grocery",
)
LOCATIONS = (
    ("US", "New York"),
    ("US", "Chicago"),
    ("US", "Austin"),
    ("CA", "Toronto"),
    ("CA", "Vancouver"),
    ("GB", "London"),
    ("GB", "Manchester"),
    ("DE", "Berlin"),
    ("DE", "Hamburg"),
    ("FR", "Paris"),
    ("AU", "Sydney"),
    ("JP", "Osaka"),
)
LOCATION_WEIGHTS = (18, 10, 9, 10, 4, 12, 5, 8, 4, 8, 6, 6)
FIRST_NAMES = (
    "Alex",
    "Amelia",
    "Avery",
    "Ella",
    "Maya",
    "Mia",
    "Noah",
    "Owen",
    "Sam",
    "Taylor",
)
LAST_NAMES = (
    "Brooks",
    "Chen",
    "Ellis",
    "Green",
    "Kim",
    "Martin",
    "Patel",
    "Reed",
    "Rivera",
    "Shaw",
)
ORDER_OUTCOMES = (
    OrderStatus.PAID,
    OrderStatus.SHIPPED,
    OrderStatus.DELIVERED,
    OrderStatus.CANCELLED,
    OrderStatus.REFUNDED,
)
PAYMENT_STATUS_BY_ORDER = {
    OrderStatus.PENDING: PaymentStatus.PENDING,
    OrderStatus.PAID: PaymentStatus.PAID,
    OrderStatus.SHIPPED: PaymentStatus.PAID,
    OrderStatus.DELIVERED: PaymentStatus.PAID,
    OrderStatus.CANCELLED: PaymentStatus.FAILED,
    OrderStatus.REFUNDED: PaymentStatus.REFUNDED,
}
EVENT_TYPES = ("page_view", "product_view", "search", "add_to_cart", "checkout")
COHORT_START = datetime(2023, 1, 1, tzinfo=UTC)
ACTIVITY_START = datetime(2024, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    users: int
    orders: int
    events: int
    seed: int

    def __post_init__(self) -> None:
        if self.users < 1 or self.orders < 0 or self.events < 0:
            raise ValueError(
                "users must be positive, orders and events must be non-negative"
            )

    @property
    def products(self) -> int:
        return min(10_000, max(100, (self.users + 9) // 10))

    @property
    def fixed_counts(self) -> dict[str, int]:
        return {
            "categories": len(CATEGORY_NAMES),
            "users": self.users,
            "products": self.products,
            "orders": self.orders,
            "payments": self.orders,
            "user_events": self.events,
            "order_status_history": 2 * self.orders,
        }


@dataclass(frozen=True, slots=True)
class OrderRows:
    order: Row
    items: tuple[Row, ...]
    payment: Row
    history: tuple[Row, ...]


def category_rows() -> Iterator[Row]:
    for category_id, name in enumerate(CATEGORY_NAMES, start=1):
        yield category_id, name, name.lower().replace(" ", "-"), COHORT_START


def user_rows(spec: DatasetSpec) -> Iterator[Row]:
    rng = Random(f"users:{spec.seed}")
    for user_id in range(1, spec.users + 1):
        country, city = rng.choices(LOCATIONS, weights=LOCATION_WEIGHTS)[0]
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        email = f"{first.lower()}.{last.lower()}.{user_id}@example.test"
        status = rng.choices(tuple(UserStatus), weights=(87, 11, 2))[0]
        created_at = COHORT_START + timedelta(seconds=rng.randrange(365 * 86_400))
        yield (
            user_id,
            email,
            f"{first} {last}",
            city,
            country,
            status.value,
            created_at,
            created_at,
        )


def product_price(product_id: int, seed: int) -> Decimal:
    cents = 199 + (product_id * 7919 + seed * 104729) % 50_000
    return Decimal(cents).scaleb(-2)


def product_rows(spec: DatasetSpec) -> Iterator[Row]:
    rng = Random(f"products:{spec.seed}")
    for product_id in range(1, spec.products + 1):
        category_id = rng.randrange(1, len(CATEGORY_NAMES) + 1)
        yield (
            product_id,
            category_id,
            f"{CATEGORY_NAMES[category_id - 1]} Item {product_id}",
            f"LAB-{product_id:05d}",
            product_price(product_id, spec.seed),
            rng.random() < 0.91,
            COHORT_START + timedelta(seconds=rng.randrange(365 * 86_400)),
        )


def activity_time(rng: Random) -> datetime:
    earliest_day = 365 if rng.random() < 0.65 else 0
    return ACTIVITY_START + timedelta(
        days=rng.randrange(earliest_day, 700), seconds=rng.randrange(86_400)
    )


def popular_id(rng: Random, population: int) -> int:
    upper_bound = max(1, population // 5) if rng.random() < 0.7 else population
    return rng.randint(1, upper_bound)


def order_path(rng: Random, order_id: int, order_count: int) -> tuple[OrderStatus, ...]:
    # A pending order contributes one entry and the following delivered order contributes three.
    if order_id % 20 == 1 and order_id < order_count:
        return (OrderStatus.PENDING,)
    if order_id % 20 == 2:
        return OrderStatus.PENDING, OrderStatus.SHIPPED, OrderStatus.DELIVERED

    outcome = rng.choices(ORDER_OUTCOMES, weights=(7, 9, 68, 12, 4))[0]
    return OrderStatus.PENDING, outcome


def order_rows(spec: DatasetSpec) -> Iterator[OrderRows]:
    rng = Random(f"orders:{spec.seed}")
    item_id = history_id = 0
    for order_id in range(1, spec.orders + 1):
        user_id = popular_id(rng, spec.users)
        created_at = activity_time(rng)
        path = order_path(rng, order_id, spec.orders)

        items = []
        total = Decimal("0.00")
        for _ in range(rng.choice((2, 2, 3, 3, 3, 4))):
            item_id += 1
            product_id = popular_id(rng, spec.products)
            quantity = rng.choices((1, 2, 3, 4, 5), weights=(70, 16, 8, 4, 2))[0]
            price = product_price(product_id, spec.seed)
            items.append((item_id, order_id, product_id, quantity, price))
            total += quantity * price

        history = []
        old_status = None
        changed_at = created_at
        for status in path:
            history_id += 1
            history.append((history_id, order_id, old_status, status.value, changed_at))
            old_status = status.value
            changed_at += timedelta(hours=rng.randint(1, 48))

        payment_status = PAYMENT_STATUS_BY_ORDER[path[-1]]
        paid_at = None
        if payment_status in (PaymentStatus.PAID, PaymentStatus.REFUNDED):
            paid_at = created_at + timedelta(minutes=rng.randrange(1, 60))
        yield OrderRows(
            (order_id, user_id, path[-1].value, total, created_at, history[-1][4]),
            tuple(items),
            (order_id, order_id, payment_status.value, total, paid_at, created_at),
            tuple(history),
        )


def event_rows(spec: DatasetSpec) -> Iterator[Row]:
    rng = Random(f"events:{spec.seed}")
    for event_id in range(1, spec.events + 1):
        user_id = popular_id(rng, spec.users)
        event_type = rng.choices(EVENT_TYPES, weights=(39, 29, 17, 11, 4))[0]
        payload = {
            "source": rng.choices(
                ("direct", "search", "campaign", "referral"), weights=(45, 32, 16, 7)
            )[0],
            "device": rng.choices(("mobile", "desktop", "tablet"), weights=(61, 34, 5))[
                0
            ],
        }
        if event_type in ("product_view", "add_to_cart"):
            payload["product_id"] = popular_id(rng, spec.products)
        elif event_type == "search":
            payload["query"] = rng.choice(CATEGORY_NAMES).lower()
        yield (
            event_id,
            user_id,
            event_type,
            json.dumps(payload, separators=(",", ":")),
            activity_time(rng),
        )
