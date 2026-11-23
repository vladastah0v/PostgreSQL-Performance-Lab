from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.types import CreatedAt, PrimaryKey, UpdatedAt, status_type

if TYPE_CHECKING:
    from app.models.event import UserEvent
    from app.models.order import Order


class UserStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    BLOCKED = "blocked"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("country ~ '^[A-Z]{2}$'", name="country_format"),)

    id: Mapped[PrimaryKey]
    email: Mapped[str] = mapped_column(String(254), unique=True)
    full_name: Mapped[str] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(120))
    country: Mapped[str] = mapped_column(String(2))
    status: Mapped[UserStatus] = mapped_column(
        status_type(UserStatus, "status"), server_default=UserStatus.ACTIVE
    )
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    orders: Mapped[list[Order]] = relationship(
        back_populates="user", lazy="raise", passive_deletes="all"
    )
    events: Mapped[list[UserEvent]] = relationship(
        back_populates="user", lazy="raise", passive_deletes="all"
    )


Index("ix_users_city_status_id", func.lower(User.city), User.status, User.id)
