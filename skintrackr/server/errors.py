"""Error responses, in Echo's `{"message": "..."}` shape which existing API clients expect."""

from collections.abc import Generator
from contextlib import contextmanager

from litestar import Request, Response
from litestar.exceptions import HTTPException

from skintrackr.logging import get_logger

logger = get_logger(__name__)

INTERNAL_SERVER_ERROR = "Internal Server Error"


def http_exception_handler(_: Request, exc: HTTPException) -> Response[dict[str, str]]:
    """Render an HTTPException (raised by handlers, or by Litestar for e.g. unknown routes)."""
    return Response({"message": exc.detail}, status_code=exc.status_code, headers=exc.headers)


def internal_error_handler(request: Request, exc: Exception) -> Response[dict[str, str]]:
    """Log an unhandled error with its traceback and answer 500."""
    logger.error("unhandled error", exc_info=exc, extra={"method": request.method, "uri": request.url.path})
    return Response({"message": INTERNAL_SERVER_ERROR}, status_code=500)


@contextmanager
def fail_with(message: str, status_code: int = 500, /, **context: object) -> Generator[None]:
    """Turn any error in the block into an HTTP error with `message`, logging the cause with `context`.

    HTTPExceptions raised in the block pass through unchanged.

        with fail_with("failed to fetch activity", activity_id=activity_id):
            activity = await strava.get_activity(activity_id)
    """
    try:
        yield
    except HTTPException:
        raise
    except Exception as e:
        logger.error(message, exc_info=e, extra=context)
        raise HTTPException(status_code=status_code, detail=message) from e
