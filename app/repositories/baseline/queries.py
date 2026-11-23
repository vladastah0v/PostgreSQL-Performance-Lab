from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Date, and_, cast, func, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from app.models import Order, OrderItem, OrderStatus, Payment, Product, User, UserEvent
from app.repositories.commerce import OrderDetailsRows, UserOrders
from app.repositories.filters import user_filters
from app.schemas.parameters import (
    DateRange,
    EventSearch,
    OrderSearch,
    SalesQuery,
    TopProductsQuery,
    UserSearch,
)


def calendar_date_filters(
    column: InstrumentedAttribute[datetime], query: DateRange
) -> list[ColumnElement[bool]]:
    # Casting every timestamp makes these predicates non-sargable on a plain timestamp index.
    day = cast(column, Date)
    predicates = []
    if query.date_from is not None:
        predicates.append(day >= query.date_from)
    if query.date_to is not None:
        predicates.append(day <= query.date_to)
    return predicates


class BaselineRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def search_users(self, query: UserSearch) -> Sequence[User]:
        # A full ORM projection loads timestamps the response never uses.
        statement = (
            select(User)
            .where(*user_filters(query))
            .order_by(User.id)
            .offset(query.offset)
            .limit(query.limit + 1)
        )
        return (await self.session.scalars(statement)).all()

    async def user(self, user_id: int) -> User | None:
        return await self.session.get(User, user_id)

    async def user_orders(self, user_id: int, query: OrderSearch) -> UserOrders | None:
        if await self.user(user_id) is None:
            return None
        orders = await self.orders(query, user_id=user_id)
        # Deliberate N+1: load every line item for each order to compute its item count.
        items = [
            (order, len(await self.order_items(order.id)))
            for order in orders[: query.limit]
        ]
        return items, len(orders) > query.limit

    async def orders(
        self, query: OrderSearch, user_id: int | None = None
    ) -> Sequence[Order]:
        statement = (
            select(Order)
            .where(*calendar_date_filters(Order.created_at, query))
            .order_by(Order.created_at.desc(), Order.id.desc())
            .offset(query.offset)
            .limit(query.limit + 1)
        )
        if query.status is not None:
            statement = statement.where(Order.status == query.status)
        if user_id is not None:
            statement = statement.where(Order.user_id == user_id)
        return (await self.session.scalars(statement)).all()

    async def order(self, order_id: int) -> Order | None:
        return await self.session.get(Order, order_id)

    async def order_items(self, order_id: int) -> Sequence[OrderItem]:
        statement = (
            select(OrderItem)
            .where(OrderItem.order_id == order_id)
            .order_by(OrderItem.id)
        )
        return (await self.session.scalars(statement)).all()

    async def payments(self, order_id: int) -> Sequence[Payment]:
        statement = (
            select(Payment).where(Payment.order_id == order_id).order_by(Payment.id)
        )
        return (await self.session.scalars(statement)).all()

    async def order_details(self, order_id: int) -> OrderDetailsRows | None:
        order = await self.order(order_id)
        if order is None:
            return None
        return order, await self.order_items(order_id), await self.payments(order_id)

    async def sales(
        self, query: SalesQuery, statuses: tuple[OrderStatus, ...]
    ) -> Sequence[RowMapping]:
        period = cast(func.date_trunc(query.period, Order.created_at), Date).label(
            "period"
        )
        statement = (
            select(
                period,
                func.count().label("order_count"),
                func.sum(Order.total_amount).label("revenue"),
            )
            .where(
                Order.status.in_(statuses),
                *calendar_date_filters(Order.created_at, query),
            )
            .group_by(period)
            .order_by(period)
        )
        return (await self.session.execute(statement)).mappings().all()

    async def top_products(
        self, query: TopProductsQuery, statuses: tuple[OrderStatus, ...]
    ) -> Sequence[RowMapping]:
        units_sold = func.sum(OrderItem.quantity).label("units_sold")
        revenue = func.sum(OrderItem.quantity * OrderItem.unit_price).label("revenue")
        # The aggregate follows the joins and groups product attributes with the fact rows.
        statement = (
            select(Product.id.label("product_id"), Product.name, units_sold, revenue)
            .select_from(Product)
            .join(OrderItem, OrderItem.product_id == Product.id)
            .join(Order, Order.id == OrderItem.order_id)
            .where(
                Order.status.in_(statuses),
                *calendar_date_filters(Order.created_at, query),
            )
            .group_by(Product.id, Product.name)
            .order_by(units_sold.desc(), revenue.desc(), Product.id)
            .limit(query.limit)
        )
        return (await self.session.execute(statement)).mappings().all()

    async def customers(
        self, query: DateRange, statuses: tuple[OrderStatus, ...]
    ) -> RowMapping:
        # Join all customers before grouping, including those with no qualifying orders.
        per_customer = (
            select(
                User.id,
                func.count(Order.id).label("order_count"),
                func.coalesce(func.sum(Order.total_amount), 0).label("revenue"),
            )
            .select_from(User)
            .outerjoin(
                Order,
                and_(
                    Order.user_id == User.id,
                    Order.status.in_(statuses),
                    *calendar_date_filters(Order.created_at, query),
                ),
            )
            .group_by(User.id)
            .subquery()
        )
        statement = select(
            func.count().label("total_customers"),
            func.count()
            .filter(per_customer.c.order_count > 0)
            .label("customers_with_orders"),
            func.count()
            .filter(per_customer.c.order_count > 1)
            .label("repeat_customers"),
            func.coalesce(func.sum(per_customer.c.order_count), 0).label("order_count"),
            func.coalesce(func.sum(per_customer.c.revenue), 0).label("revenue"),
        ).select_from(per_customer)
        return (await self.session.execute(statement)).mappings().one()

    async def events(self, query: EventSearch) -> Sequence[UserEvent]:
        statement = (
            select(UserEvent)
            .where(
                UserEvent.user_id == query.user_id,
                *calendar_date_filters(UserEvent.created_at, query),
            )
            .order_by(UserEvent.created_at.desc(), UserEvent.id.desc())
            .offset(query.offset)
            .limit(query.limit + 1)
        )
        if query.event_type is not None:
            statement = statement.where(UserEvent.event_type == query.event_type)
        return (await self.session.scalars(statement)).all()
