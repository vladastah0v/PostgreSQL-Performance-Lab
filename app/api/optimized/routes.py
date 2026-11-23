from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.repositories.optimized.queries import OptimizedRepository
from app.schemas.cursors import NEXT_CURSOR_HEADER, encode_cursor
from app.schemas.parameters import (
    MAX_ID,
    CursorEventSearch,
    CursorOrderSearch,
    CursorPagination,
    CursorUserSearch,
    DateRange,
    SalesQuery,
    TopProductsQuery,
)
from app.schemas.responses import (
    CustomerStats,
    EventSummary,
    OrderDetails,
    OrderSummary,
    Page,
    ProductSales,
    SalesBucket,
    UserOrder,
    UserSummary,
)
from app.services.commerce import CommerceService

router = APIRouter(prefix="/api/v1/optimized", tags=["optimized"])
EntityId = Annotated[int, Path(ge=1, le=MAX_ID)]


async def get_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CommerceService:
    return CommerceService(OptimizedRepository(session))


Service = Annotated[CommerceService, Depends(get_service)]


def set_next_cursor[T: UserSummary | OrderSummary | EventSummary](
    response: Response, page: Page[T], query: CursorPagination, resource: str
) -> Page[T]:
    if query.cursor is not None and page.has_more:
        last = page.items[-1]
        created_at = (
            last.created_at if isinstance(last, OrderSummary | EventSummary) else None
        )
        response.headers[NEXT_CURSOR_HEADER] = encode_cursor(
            query, resource, last.id, created_at
        )
    return page


@router.get("/users/search")
async def search_users(
    query: Annotated[CursorUserSearch, Query()], service: Service, response: Response
) -> Page[UserSummary]:
    page = await service.search_users(query)
    return set_next_cursor(response, page, query, "users/search")


@router.get("/users/{user_id}/orders")
async def user_orders(
    user_id: EntityId,
    query: Annotated[CursorOrderSearch, Query()],
    service: Service,
    response: Response,
) -> Page[UserOrder]:
    page = await service.user_orders(user_id, query)
    return set_next_cursor(response, page, query, f"users/{user_id}/orders")


@router.get("/orders")
async def orders(
    query: Annotated[CursorOrderSearch, Query()], service: Service, response: Response
) -> Page[OrderSummary]:
    page = await service.orders(query)
    return set_next_cursor(response, page, query, "orders")


@router.get("/orders/{order_id}/details")
async def order_details(order_id: EntityId, service: Service) -> OrderDetails:
    return await service.order_details(order_id)


@router.get("/analytics/sales")
async def sales(
    query: Annotated[SalesQuery, Query()], service: Service
) -> list[SalesBucket]:
    return await service.sales(query)


@router.get("/analytics/top-products")
async def top_products(
    query: Annotated[TopProductsQuery, Query()], service: Service
) -> list[ProductSales]:
    return await service.top_products(query)


@router.get("/analytics/customers")
async def customers(
    query: Annotated[DateRange, Query()], service: Service
) -> CustomerStats:
    return await service.customers(query)


@router.get("/events")
async def events(
    query: Annotated[CursorEventSearch, Query()], service: Service, response: Response
) -> Page[EventSummary]:
    page = await service.events(query)
    return set_next_cursor(response, page, query, "events")
