from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models import OrderStatus, PaymentStatus, UserStatus


class Page[T](BaseModel):
    items: list[T]
    limit: int
    offset: int
    has_more: bool


class Record(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserSummary(Record):
    id: int
    email: str
    full_name: str
    city: str
    country: str
    status: UserStatus


class OrderSummary(Record):
    id: int
    user_id: int
    status: OrderStatus
    total_amount: Decimal
    created_at: datetime


class UserOrder(OrderSummary):
    item_count: int


class ItemSummary(Record):
    id: int
    product_id: int
    quantity: int
    unit_price: Decimal


class PaymentSummary(Record):
    id: int
    status: PaymentStatus
    amount: Decimal
    paid_at: datetime | None
    created_at: datetime


class OrderDetails(OrderSummary):
    updated_at: datetime
    items: list[ItemSummary]
    payments: list[PaymentSummary]


class EventSummary(Record):
    id: int
    user_id: int
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


class SalesBucket(BaseModel):
    period: date
    order_count: int
    revenue: Decimal


class ProductSales(BaseModel):
    product_id: int
    name: str
    units_sold: int
    revenue: Decimal


class CustomerStats(BaseModel):
    total_customers: int
    customers_with_orders: int
    repeat_customers: int
    order_count: int
    revenue: Decimal
