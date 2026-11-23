from collections.abc import Callable, Sequence

from app.core.exceptions import ResourceNotFound
from app.models.order import SALES_STATUSES
from app.repositories.commerce import CommerceRepository
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


def make_page[T, R](
    rows: Sequence[R], query: OffsetParams, convert: Callable[[R], T]
) -> Page[T]:
    return Page(
        items=[convert(row) for row in rows[: query.limit]],
        limit=query.limit,
        offset=query.offset,
        has_more=len(rows) > query.limit,
    )


class CommerceService:
    def __init__(self, repository: CommerceRepository):
        self.repository = repository

    async def search_users(self, query: UserSearch) -> Page[UserSummary]:
        return make_page(
            await self.repository.search_users(query), query, UserSummary.model_validate
        )

    async def user_orders(self, user_id: int, query: OrderSearch) -> Page[UserOrder]:
        orders = await self.repository.user_orders(user_id, query)
        if orders is None:
            raise ResourceNotFound("User not found")
        rows, has_more = orders
        return Page(
            items=[
                UserOrder(
                    **OrderSummary.model_validate(order).model_dump(),
                    item_count=item_count,
                )
                for order, item_count in rows
            ],
            limit=query.limit,
            offset=query.offset,
            has_more=has_more,
        )

    async def orders(self, query: OrderSearch) -> Page[OrderSummary]:
        return make_page(
            await self.repository.orders(query), query, OrderSummary.model_validate
        )

    async def order_details(self, order_id: int) -> OrderDetails:
        details = await self.repository.order_details(order_id)
        if details is None:
            raise ResourceNotFound("Order not found")
        order, items, payments = details
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
