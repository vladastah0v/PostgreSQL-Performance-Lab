from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, String, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.types import CreatedAt, Money, PrimaryKey, nonnegative_money

if TYPE_CHECKING:
    from app.models.category import Category
    from app.models.order import OrderItem


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (nonnegative_money("price"),)

    id: Mapped[PrimaryKey]
    category_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("categories.id"))
    name: Mapped[str] = mapped_column(String(200))
    sku: Mapped[str] = mapped_column(String(64), unique=True)
    price: Mapped[Money]
    is_active: Mapped[bool] = mapped_column(server_default=true())
    created_at: Mapped[CreatedAt]

    category: Mapped[Category] = relationship(back_populates="products", lazy="raise")
    order_items: Mapped[list[OrderItem]] = relationship(
        back_populates="product", lazy="raise", passive_deletes="all"
    )
