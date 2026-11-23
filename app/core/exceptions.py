import logging

from fastapi import Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.schemas.cursors import InvalidCursor

logger = logging.getLogger(__name__)


class ResourceNotFound(Exception):
    pass


async def resource_not_found(request: Request, error: ResourceNotFound) -> JSONResponse:
    return JSONResponse({"detail": str(error)}, status_code=status.HTTP_404_NOT_FOUND)


async def invalid_cursor(request: Request, error: InvalidCursor) -> JSONResponse:
    return JSONResponse(
        {"detail": str(error)}, status_code=status.HTTP_422_UNPROCESSABLE_CONTENT
    )


async def database_error(request: Request, error: SQLAlchemyError) -> JSONResponse:
    code = getattr(error.orig, "sqlstate", "") if isinstance(error, DBAPIError) else ""
    logger.warning(
        "Database request failed (%s, SQLSTATE %s)", type(error).__name__, code
    )
    if code == "57014":
        return JSONResponse(
            {"detail": "Database query was canceled"},
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        )
    if isinstance(error, PoolTimeoutError) or (
        isinstance(error, DBAPIError)
        and (error.connection_invalidated or (code or "").startswith("08"))
    ):
        return JSONResponse(
            {"detail": "Database is unavailable"},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return JSONResponse(
        {"detail": "Database request failed"},
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
