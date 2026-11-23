from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import OrderStatus, UserStatus

MAX_ID = 2**63 - 1


class QueryParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OffsetParams(QueryParams):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=2**31 - 1)


class DateRange(QueryParams):
    date_from: date | None = None
    date_to: date | None = None

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if (
            self.date_from is not None
            and self.date_to is not None
            and self.date_from > self.date_to
        ):
            raise ValueError("date_from must be earlier than or equal to date_to")
        return self


class UserSearch(OffsetParams):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    city: str | None = Field(default=None, min_length=1, max_length=120)
    email: str | None = Field(default=None, min_length=1, max_length=254)
    status: UserStatus | None = None


class OrderSearch(DateRange, OffsetParams):
    status: OrderStatus | None = None


class EventSearch(DateRange, OffsetParams):
    user_id: int = Field(ge=1, le=MAX_ID)
    event_type: str | None = Field(default=None, min_length=1, max_length=64)


class SalesQuery(DateRange):
    period: Literal["day", "month"] = "day"


class TopProductsQuery(DateRange):
    limit: int = Field(default=20, ge=1, le=100)


class CursorPagination(OffsetParams):
    cursor: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def validate_pagination(self) -> Self:
        if self.cursor is not None and self.offset != 0:
            raise ValueError("cursor cannot be combined with a nonzero offset")
        return self


class CursorUserSearch(UserSearch, CursorPagination):
    pass


class CursorOrderSearch(OrderSearch, CursorPagination):
    pass


class CursorEventSearch(EventSearch, CursorPagination):
    pass
