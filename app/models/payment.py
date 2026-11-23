from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.types import (
    CreatedAt,
    Money,
    PrimaryKey,
    nonnegative_money,
    status_type,
)

if TYPE_CHECKING:
    from app.models.order import Order


class PaymentStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    REFUNDED = "refunded"


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        nonnegative_money("amount"),
        CheckConstraint(
            "(status IN ('paid', 'refunded')) = (paid_at IS NOT NULL)",
            name="paid_at_matches_status",
        ),
    )

    id: Mapped[PrimaryKey]
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("orders.id"))
    status: Mapped[PaymentStatus] = mapped_column(
        status_type(PaymentStatus, "status"), server_default=PaymentStatus.PENDING
    )
    amount: Mapped[Money]
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[CreatedAt]

    order: Mapped[Order] = relationship(back_populates="payments", lazy="raise")
