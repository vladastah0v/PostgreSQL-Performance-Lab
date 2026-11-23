from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Category,
    Order,
    OrderItem,
    OrderStatus,
    OrderStatusHistory,
    Payment,
    PaymentStatus,
    Product,
    User,
    UserEvent,
    UserStatus,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def order(db_session: AsyncSession) -> Order:
    suffix = uuid4().hex
    user = User(
        email=f"{suffix}@example.test",
        full_name="Test Customer",
        city="New York",
        country="US",
        events=[UserEvent(event_type="order_created", payload={"source": "web"})],
    )
    unit_price = Decimal("9.95")
    product = Product(
        category=Category(name="Test Category", slug=f"test-{suffix}"),
        name="Test Product",
        sku=f"test-{suffix}",
        price=unit_price,
    )
    order = Order(
        user=user,
        total_amount=Decimal("19.90"),
        items=[OrderItem(product=product, quantity=2, unit_price=unit_price)],
        payments=[Payment(amount=Decimal("19.90"))],
        status_history=[OrderStatusHistory(new_status=OrderStatus.PENDING)],
    )
    db_session.add(order)
    await db_session.flush()
    return order


async def test_order_graph_round_trips(db_session: AsyncSession, order: Order) -> None:
    order_id = order.id
    db_session.expunge_all()
    query = (
        select(Order)
        .where(Order.id == order_id)
        .options(
            selectinload(Order.user).selectinload(User.events),
            selectinload(Order.items)
            .selectinload(OrderItem.product)
            .selectinload(Product.category),
            selectinload(Order.payments),
            selectinload(Order.status_history),
        )
    )
    loaded = (await db_session.scalars(query)).one()

    assert loaded.id > 0
    assert loaded.created_at.utcoffset() == timedelta(0)
    assert loaded.status is OrderStatus.PENDING
    assert loaded.user.status is UserStatus.ACTIVE
    assert loaded.user.events[0].payload == {"source": "web"}
    assert loaded.total_amount == Decimal("19.90")
    [item] = loaded.items
    assert item.unit_price * item.quantity == loaded.total_amount
    assert item.product.is_active is True
    assert item.product.category.name == "Test Category"
    [payment] = loaded.payments
    assert payment.amount == loaded.total_amount
    assert payment.status is PaymentStatus.PENDING
    assert payment.paid_at is None
    [transition] = loaded.status_history
    assert transition.old_status is None
    assert transition.new_status is OrderStatus.PENDING


@pytest.mark.parametrize(
    ("column", "value", "sqlstate"),
    [
        ("full_name", None, "23502"),
        ("status", "invalid", "23514"),
        ("country", "us", "23514"),
    ],
)
async def test_user_constraints(
    db_session: AsyncSession, column: str, value: str | None, sqlstate: str
) -> None:
    values: dict[str, str | None] = {
        "email": f"{uuid4().hex}@example.test",
        "full_name": "Test Customer",
        "city": "New York",
        "country": "US",
    }
    values[column] = value

    with pytest.raises(IntegrityError) as error:
        await db_session.execute(insert(User).values(**values))
    assert error.value.orig.sqlstate == sqlstate


async def test_duplicate_email_is_rejected(
    db_session: AsyncSession, order: Order
) -> None:
    with pytest.raises(IntegrityError) as error:
        await db_session.execute(
            insert(User).values(
                email=order.user.email,
                full_name="Duplicate",
                city="New York",
                country="US",
            )
        )
    assert error.value.orig.sqlstate == "23505"


async def test_order_requires_an_existing_user(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError) as error:
        await db_session.execute(
            insert(Order).values(user_id=-1, total_amount=Decimal("9.95"))
        )
    assert error.value.orig.sqlstate == "23503"


@pytest.mark.parametrize(
    ("quantity", "unit_price"),
    [(0, Decimal("9.95")), (1, Decimal("-0.01")), (1, Decimal("NaN"))],
)
async def test_order_item_quantity_and_price_constraints(
    db_session: AsyncSession, order: Order, quantity: int, unit_price: Decimal
) -> None:
    with pytest.raises(IntegrityError) as error:
        await db_session.execute(
            insert(OrderItem).values(
                order_id=order.id,
                product_id=order.items[0].product_id,
                quantity=quantity,
                unit_price=unit_price,
            )
        )
    assert error.value.orig.sqlstate == "23514"


async def test_paid_payment_requires_a_timestamp(
    db_session: AsyncSession, order: Order
) -> None:
    with pytest.raises(IntegrityError) as error:
        await db_session.execute(
            insert(Payment).values(
                order_id=order.id, amount=order.total_amount, status=PaymentStatus.PAID
            )
        )
    assert error.value.orig.sqlstate == "23514"


async def test_history_requires_a_status_change(
    db_session: AsyncSession, order: Order
) -> None:
    with pytest.raises(IntegrityError) as error:
        await db_session.execute(
            insert(OrderStatusHistory).values(
                order_id=order.id,
                old_status=OrderStatus.PENDING,
                new_status=OrderStatus.PENDING,
            )
        )
    assert error.value.orig.sqlstate == "23514"


async def test_event_payload_must_be_an_object(
    db_session: AsyncSession, order: Order
) -> None:
    with pytest.raises(IntegrityError) as error:
        await db_session.execute(
            insert(UserEvent).values(
                user_id=order.user_id, event_type="invalid", payload=[1, 2]
            )
        )
    assert error.value.orig.sqlstate == "23514"
