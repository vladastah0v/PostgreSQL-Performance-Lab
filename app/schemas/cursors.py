import base64
import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.schemas.parameters import MAX_ID, CursorPagination, OffsetParams

START_CURSOR = "start"
NEXT_CURSOR_HEADER = "X-Next-Cursor"


class InvalidCursor(ValueError):
    pass


class CursorAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: str
    id: Annotated[int, Field(ge=1, le=MAX_ID, strict=True)]
    created_at: AwareDatetime | None = None

    @field_validator("created_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        try:
            return value.astimezone(UTC) if value is not None else None
        except OverflowError:
            raise ValueError("Cursor timestamp is out of range") from None


def cursor_scope(query: OffsetParams, resource: str) -> str:
    filters = query.model_dump(mode="json", exclude={"cursor", "limit", "offset"})
    payload = json.dumps([resource, filters], sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode()).hexdigest()


def encode_cursor(
    query: OffsetParams, resource: str, id: int, created_at: datetime | None = None
) -> str:
    anchor = CursorAnchor(
        scope=cursor_scope(query, resource), id=id, created_at=created_at
    )
    return (
        base64.urlsafe_b64encode(anchor.model_dump_json().encode()).decode().rstrip("=")
    )


def decode_cursor(
    query: OffsetParams, resource: str, *, chronological: bool = False
) -> CursorAnchor | None:
    if not isinstance(query, CursorPagination) or query.cursor in (None, START_CURSOR):
        return None

    token = query.cursor
    try:
        raw = base64.b64decode(
            token + "=" * (-len(token) % 4), altchars=b"-_", validate=True
        )
        anchor = CursorAnchor.model_validate_json(raw)
    except ValueError:
        raise InvalidCursor("Invalid pagination cursor") from None
    if (
        anchor.scope != cursor_scope(query, resource)
        or (anchor.created_at is not None) != chronological
    ):
        raise InvalidCursor(
            "Pagination cursor does not match this resource and its filters"
        )
    return anchor
