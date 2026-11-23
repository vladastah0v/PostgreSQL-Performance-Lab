from collections.abc import Callable, Sequence

from app.core.exceptions import ResourceNotFound
from app.models import OrderStatus
from app.repositories.baseline.queries import BaselineRepository
from app.schemas.parameters import (
    DateRange,
    EventSearch,
    OffsetParams,
    OrderSearch,
    SalesQuery,
    TopProductsQuery,
    UserSearch,
)
from app.schemas.responses import (
    CustomerStats,
    EventSummary,
    ItemSummary,
    OrderDetails,
    OrderSummary,
    Page,
    PaymentSummary,
    ProductSales,
    SalesBucket,
    UserOrder,
    UserSummary,
)

SALES_STATUSES = (OrderStatus.PAID, OrderStatus.SHIPPED, OrderStatus.DELIVERED)


def make_page[T, R](
    rows: Sequence[R], query: OffsetParams, convert: Callable[[R], T]
) -> Page[T]:
    return Page(
        items=[convert(row) for row in rows[: query.limit]],
        limit=query.limit,
        offset=query.offset,
        has_more=len(rows) > query.limit,
    )


class BaselineService:
    def __init__(self, repository: BaselineRepository):
        self.repository = repository

    async def search_users(self, query: UserSearch) -> Page[UserSummary]:
        return make_page(
            await self.repository.search_users(query), query, UserSummary.model_validate
        )

    async def user_orders(self, user_id: int, query: OrderSearch) -> Page[UserOrder]:
        if await self.repository.user(user_id) is None:
            raise ResourceNotFound("User not found")
        orders = await self.repository.orders(query, user_id=user_id)

        # Deliberate N+1: load every line item for each order to compute its item count.
        items = []
        for order in orders[: query.limit]:
            order_items = await self.repository.order_items(order.id)
            items.append(
                UserOrder(
                    **OrderSummary.model_validate(order).model_dump(),
                    item_count=len(order_items),
                )
            )
        return Page(
            items=items,
            limit=query.limit,
            offset=query.offset,
            has_more=len(orders) > query.limit,
        )

    async def orders(self, query: OrderSearch) -> Page[OrderSummary]:
        return make_page(
            await self.repository.orders(query), query, OrderSummary.model_validate
        )

    async def order_details(self, order_id: int) -> OrderDetails:
        order = await self.repository.order(order_id)
        if order is None:
            raise ResourceNotFound("Order not found")
        items = await self.repository.order_items(order_id)
        payments = await self.repository.payments(order_id)
        return OrderDetails(
            **OrderSummary.model_validate(order).model_dump(),
            updated_at=order.updated_at,
            items=[ItemSummary.model_validate(item) for item in items],
            payments=[PaymentSummary.model_validate(payment) for payment in payments],
        )

    async def sales(self, query: SalesQuery) -> list[SalesBucket]:
        return [
            SalesBucket.model_validate(row)
            for row in await self.repository.sales(query, SALES_STATUSES)
        ]

    async def top_products(self, query: TopProductsQuery) -> list[ProductSales]:
        return [
            ProductSales.model_validate(row)
            for row in await self.repository.top_products(query, SALES_STATUSES)
        ]

    async def customers(self, query: DateRange) -> CustomerStats:
        return CustomerStats.model_validate(
            await self.repository.customers(query, SALES_STATUSES)
        )

    async def events(self, query: EventSearch) -> Page[EventSummary]:
        return make_page(
            await self.repository.events(query), query, EventSummary.model_validate
        )
