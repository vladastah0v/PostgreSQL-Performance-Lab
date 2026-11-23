from collections.abc import Sequence
from typing import Protocol

from sqlalchemy.engine import RowMapping

from app.models import Order, OrderItem, OrderStatus, Payment, User, UserEvent
from app.schemas.parameters import (
    DateRange,
    EventSearch,
    OrderSearch,
    SalesQuery,
    TopProductsQuery,
    UserSearch,
)

type UserOrders = tuple[list[tuple[Order, int]], bool]
type OrderDetailsRows = tuple[Order, Sequence[OrderItem], Sequence[Payment]]


class CommerceRepository(Protocol):
    async def search_users(self, query: UserSearch) -> Sequence[User | RowMapping]: ...

    async def user_orders(
        self, user_id: int, query: OrderSearch
    ) -> UserOrders | None: ...

    async def orders(self, query: OrderSearch) -> Sequence[Order | RowMapping]: ...

    async def order_details(self, order_id: int) -> OrderDetailsRows | None: ...

    async def sales(
        self, query: SalesQuery, statuses: tuple[OrderStatus, ...]
    ) -> Sequence[RowMapping]: ...

    async def top_products(
        self, query: TopProductsQuery, statuses: tuple[OrderStatus, ...]
    ) -> Sequence[RowMapping]: ...

    async def customers(
        self, query: DateRange, statuses: tuple[OrderStatus, ...]
    ) -> RowMapping: ...

    async def events(self, query: EventSearch) -> Sequence[UserEvent | RowMapping]: ...
