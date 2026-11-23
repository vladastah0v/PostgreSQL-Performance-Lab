from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import Date, Select, bindparam, cast, func, select, tuple_
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, joinedload, load_only, selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.models import Order, OrderItem, OrderStatus, Product, User, UserEvent
from app.repositories.commerce import OrderDetailsRows, UserOrders
from app.repositories.filters import user_filters
from app.schemas.cursors import decode_cursor
from app.schemas.parameters import (
    DateRange,
    EventSearch,
    OffsetParams,
    OrderSearch,
    SalesQuery,
    TopProductsQuery,
    UserSearch,
)

ORDER_COLUMNS = (
    Order.id,
    Order.user_id,
    Order.status,
    Order.total_amount,
    Order.created_at,
)


def timestamp_filters(
    column: InstrumentedAttribute[datetime], query: DateRange
) -> list[ColumnElement[bool]]:
    predicates = []
    if query.date_from is not None:
        predicates.append(column >= datetime.combine(query.date_from, time.min, UTC))
    if query.date_to == date.max:
        predicates.append(column <= datetime.combine(date.max, time.max, UTC))
    elif query.date_to is not None:
        predicates.append(
            column < datetime.combine(query.date_to + timedelta(days=1), time.min, UTC)
        )
    return predicates


def page_limit(statement: Select, query: OffsetParams) -> Select:
    if query.offset:
        statement = statement.offset(query.offset)
    return statement.limit(query.limit + 1)


def time_page(
    statement: Select,
    query: OffsetParams,
    resource: str,
    created_at: InstrumentedAttribute[datetime],
    id_column: InstrumentedAttribute[int],
) -> Select:
    anchor = decode_cursor(query, resource, chronological=True)
    if anchor is not None:
        statement = statement.where(
            tuple_(created_at, id_column) < (anchor.created_at, anchor.id)
        )
    return page_limit(statement.order_by(created_at.desc(), id_column.desc()), query)


def order_page(statement: Select, query: OrderSearch, resource: str) -> Select:
    statement = statement.where(*timestamp_filters(Order.created_at, query))
    if query.status is not None:
        statement = statement.where(Order.status == query.status)
    return time_page(statement, query, resource, Order.created_at, Order.id)


def sales_order_filters(
    query: DateRange, statuses: tuple[OrderStatus, ...]
) -> list[ColumnElement[bool]]:
    # These statuses are fixed by the service. SQLAlchemy renders them as safe literals so
    # PostgreSQL can prove the partial index predicate even for a generic prepared plan.
    status_values = bindparam(
        "sales_statuses",
        statuses,
        expanding=True,
        literal_execute=True,
        type_=Order.status.type,
    )
    return [
        Order.status.in_(status_values),
        *timestamp_filters(Order.created_at, query),
    ]


class OptimizedRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def search_users(self, query: UserSearch) -> Sequence[RowMapping]:
        statement = (
            select(
                User.id,
                User.email,
                User.full_name,
                User.city,
                User.country,
                User.status,
            )
            .where(*user_filters(query))
            .order_by(User.id)
        )
        if (anchor := decode_cursor(query, "users/search")) is not None:
            statement = statement.where(User.id > anchor.id)
        return (
            (await self.session.execute(page_limit(statement, query))).mappings().all()
        )

    async def orders(self, query: OrderSearch) -> Sequence[RowMapping]:
        statement = order_page(select(*ORDER_COLUMNS), query, "orders")
        return (await self.session.execute(statement)).mappings().all()

    async def user_orders(self, user_id: int, query: OrderSearch) -> UserOrders | None:
        statement = order_page(
            select(Order)
            .where(Order.user_id == user_id)
            .options(
                load_only(*ORDER_COLUMNS, raiseload=True),
                selectinload(Order.items).load_only(OrderItem.id, raiseload=True),
            ),
            query,
            f"users/{user_id}/orders",
        )
        if await self.session.scalar(select(User.id).where(User.id == user_id)) is None:
            return None
        orders = (await self.session.scalars(statement)).all()
        return [(order, len(order.items)) for order in orders[: query.limit]], len(
            orders
        ) > query.limit

    async def order_details(self, order_id: int) -> OrderDetailsRows | None:
        statement = (
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.items), joinedload(Order.payments))
        )
        order = (await self.session.scalars(statement)).unique().one_or_none()
        if order is None:
            return None
        return (
            order,
            sorted(order.items, key=lambda item: item.id),
            sorted(order.payments, key=lambda payment: payment.id),
        )

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
            .where(*sales_order_filters(query, statuses))
            .group_by(period)
            .order_by(period)
        )
        return (await self.session.execute(statement)).mappings().all()

    async def top_products(
        self, query: TopProductsQuery, statuses: tuple[OrderStatus, ...]
    ) -> Sequence[RowMapping]:
        units_sold = func.sum(OrderItem.quantity).label("units_sold")
        revenue = func.sum(OrderItem.quantity * OrderItem.unit_price).label("revenue")
        ranked = (
            select(OrderItem.product_id, units_sold, revenue)
            .join(Order, Order.id == OrderItem.order_id)
            .where(*sales_order_filters(query, statuses))
            .group_by(OrderItem.product_id)
            .order_by(units_sold.desc(), revenue.desc(), OrderItem.product_id)
            .limit(query.limit)
            .subquery()
        )
        statement = (
            select(
                ranked.c.product_id, Product.name, ranked.c.units_sold, ranked.c.revenue
            )
            .join(Product, Product.id == ranked.c.product_id)
            .order_by(
                ranked.c.units_sold.desc(), ranked.c.revenue.desc(), ranked.c.product_id
            )
        )
        return (await self.session.execute(statement)).mappings().all()

    async def customers(
        self, query: DateRange, statuses: tuple[OrderStatus, ...]
    ) -> RowMapping:
        per_customer = (
            select(
                func.count().label("order_count"),
                func.sum(Order.total_amount).label("revenue"),
            )
            .where(*sales_order_filters(query, statuses))
            .group_by(Order.user_id)
            .subquery()
        )
        total_customers = select(func.count()).select_from(User).scalar_subquery()
        statement = select(
            total_customers.label("total_customers"),
            func.count().label("customers_with_orders"),
            func.count()
            .filter(per_customer.c.order_count > 1)
            .label("repeat_customers"),
            func.coalesce(func.sum(per_customer.c.order_count), 0).label("order_count"),
            func.coalesce(func.sum(per_customer.c.revenue), 0).label("revenue"),
        ).select_from(per_customer)
        return (await self.session.execute(statement)).mappings().one()

    async def events(self, query: EventSearch) -> Sequence[RowMapping]:
        statement = select(
            UserEvent.id,
            UserEvent.user_id,
            UserEvent.event_type,
            UserEvent.payload,
            UserEvent.created_at,
        ).where(
            UserEvent.user_id == query.user_id,
            *timestamp_filters(UserEvent.created_at, query),
        )
        if query.event_type is not None:
            statement = statement.where(UserEvent.event_type == query.event_type)
        statement = time_page(
            statement, query, "events", UserEvent.created_at, UserEvent.id
        )
        return (await self.session.execute(statement)).mappings().all()
