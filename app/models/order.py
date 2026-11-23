from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.types import (
    CreatedAt,
    Money,
    PrimaryKey,
    UpdatedAt,
    nonnegative_money,
    status_type,
)

if TYPE_CHECKING:
    from app.models.payment import Payment
    from app.models.product import Product
    from app.models.user import User


class OrderStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


SALES_STATUSES = (OrderStatus.PAID, OrderStatus.SHIPPED, OrderStatus.DELIVERED)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (nonnegative_money("total_amount"),)

    id: Mapped[PrimaryKey]
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    status: Mapped[OrderStatus] = mapped_column(
        status_type(OrderStatus, "status"), server_default=OrderStatus.PENDING
    )
    total_amount: Mapped[Money]
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    user: Mapped[User] = relationship(back_populates="orders", lazy="raise")
    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order", lazy="raise", passive_deletes="all"
    )
    payments: Mapped[list[Payment]] = relationship(
        back_populates="order", lazy="raise", passive_deletes="all"
    )
    status_history: Mapped[list[OrderStatusHistory]] = relationship(
        back_populates="order", lazy="raise", passive_deletes="all"
    )


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        nonnegative_money("unit_price"),
    )

    id: Mapped[PrimaryKey]
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("orders.id"))
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id"))
    quantity: Mapped[int]
    unit_price: Mapped[Money]

    order: Mapped[Order] = relationship(back_populates="items", lazy="raise")
    product: Mapped[Product] = relationship(back_populates="order_items", lazy="raise")


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"
    __table_args__ = (
        CheckConstraint(
            "old_status IS DISTINCT FROM new_status", name="status_changed"
        ),
    )

    id: Mapped[PrimaryKey]
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("orders.id"))
    old_status: Mapped[OrderStatus | None] = mapped_column(
        status_type(OrderStatus, "old_status")
    )
    new_status: Mapped[OrderStatus] = mapped_column(
        status_type(OrderStatus, "new_status")
    )
    changed_at: Mapped[CreatedAt]

    order: Mapped[Order] = relationship(back_populates="status_history", lazy="raise")


Index("ix_orders_created_id", Order.created_at, Order.id)
Index("ix_orders_status_created_id", Order.status, Order.created_at, Order.id)
Index("ix_orders_user_created_id", Order.user_id, Order.created_at, Order.id)
Index(
    "ix_orders_sales_created_cover",
    Order.created_at,
    postgresql_include=["user_id", "total_amount"],
    postgresql_where=Order.status.in_(SALES_STATUSES),
)
Index("ix_order_items_order_id_id", OrderItem.order_id, OrderItem.id)
