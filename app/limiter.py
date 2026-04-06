import os

from fastapi import Request
from slowapi import Limiter

RATE_LIMIT = os.getenv("RATE_LIMIT", "100/minute")


def _rate_key(request: Request) -> str:
    return request.headers.get("X-API-Key") or (
        request.client.host if request.client else "unknown"
    )


limiter = Limiter(key_func=_rate_key)
