from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.lab_access import require_local_lab_client
from app.repositories.baseline.queries import BaselineRepository
from app.schemas.parameters import (
    MAX_ID,
    DateRange,
    EventSearch,
    OrderSearch,
    SalesQuery,
    TopProductsQuery,
    UserSearch,
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
from app.services.baseline import BaselineService

router = APIRouter(
    prefix="/api/v1/baseline",
    tags=["baseline"],
    dependencies=[Depends(require_local_lab_client)],
)
EntityId = Annotated[int, Path(ge=1, le=MAX_ID)]


async def get_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> BaselineService:
    return BaselineService(BaselineRepository(session))


Service = Annotated[BaselineService, Depends(get_service)]


@router.get("/users/search")
async def search_users(
    query: Annotated[UserSearch, Query()], service: Service
) -> Page[UserSummary]:
    return await service.search_users(query)


@router.get("/users/{user_id}/orders")
async def user_orders(
    user_id: EntityId, query: Annotated[OrderSearch, Query()], service: Service
) -> Page[UserOrder]:
    return await service.user_orders(user_id, query)


@router.get("/orders")
async def orders(
    query: Annotated[OrderSearch, Query()], service: Service
) -> Page[OrderSummary]:
    return await service.orders(query)


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
    query: Annotated[EventSearch, Query()], service: Service
) -> Page[EventSummary]:
    return await service.events(query)
