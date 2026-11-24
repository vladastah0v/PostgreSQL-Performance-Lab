from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel, TypeAdapter
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
from app.services.analytics_cache import AnalyticsCache, CacheBusy, Operation
from app.services.commerce import CommerceService

router = APIRouter(prefix="/api/v1/optimized", tags=["optimized"])
EntityId = Annotated[int, Path(ge=1, le=MAX_ID)]


async def get_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CommerceService:
    return CommerceService(OptimizedRepository(session))


Service = Annotated[CommerceService, Depends(get_service)]
SALES_ADAPTER = TypeAdapter(list[SalesBucket])
PRODUCTS_ADAPTER = TypeAdapter(list[ProductSales])
CUSTOMERS_ADAPTER = TypeAdapter(CustomerStats)


async def get_analytics_cache(request: Request) -> AnalyticsCache | None:
    return getattr(request.app.state, "analytics_cache", None)


Cache = Annotated[AnalyticsCache | None, Depends(get_analytics_cache)]


async def cached_analytics[T](
    cache: AnalyticsCache | None,
    operation: Operation,
    query: BaseModel,
    adapter: TypeAdapter[T],
    load: Callable[[], Awaitable[T]],
    response: Response,
) -> T:
    if cache is None:
        result, status = await load(), "BYPASS"
    else:
        try:
            result, status = await cache.get_or_load(operation, query, adapter, load)
        except CacheBusy:
            raise HTTPException(
                503,
                "Analytics result is being calculated",
                headers={"Retry-After": "1"},
            ) from None
    response.headers["X-Cache-Status"] = status
    return result


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
    query: Annotated[SalesQuery, Query()],
    service: Service,
    cache: Cache,
    response: Response,
) -> list[SalesBucket]:
    return await cached_analytics(
        cache, "sales", query, SALES_ADAPTER, lambda: service.sales(query), response
    )


@router.get("/analytics/top-products")
async def top_products(
    query: Annotated[TopProductsQuery, Query()],
    service: Service,
    cache: Cache,
    response: Response,
) -> list[ProductSales]:
    return await cached_analytics(
        cache,
        "top-products",
        query,
        PRODUCTS_ADAPTER,
        lambda: service.top_products(query),
        response,
    )


@router.get("/analytics/customers")
async def customers(
    query: Annotated[DateRange, Query()],
    service: Service,
    cache: Cache,
    response: Response,
) -> CustomerStats:
    return await cached_analytics(
        cache,
        "customers",
        query,
        CUSTOMERS_ADAPTER,
        lambda: service.customers(query),
        response,
    )


@router.get("/events")
async def events(
    query: Annotated[CursorEventSearch, Query()], service: Service, response: Response
) -> Page[EventSummary]:
    page = await service.events(query)
    return set_next_cursor(response, page, query, "events")
