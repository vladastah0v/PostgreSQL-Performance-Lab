from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.types import CreatedAt, PrimaryKey

if TYPE_CHECKING:
    from app.models.product import Product


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[PrimaryKey]
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    created_at: Mapped[CreatedAt]

    products: Mapped[list[Product]] = relationship(
        back_populates="category", lazy="raise", passive_deletes="all"
    )
