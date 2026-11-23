from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.types import CreatedAt, PrimaryKey

if TYPE_CHECKING:
    from app.models.user import User


class UserEvent(Base):
    __tablename__ = "user_events"
    __table_args__ = (
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_object"),
    )

    id: Mapped[PrimaryKey]
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[CreatedAt]

    user: Mapped[User] = relationship(back_populates="events", lazy="raise")
