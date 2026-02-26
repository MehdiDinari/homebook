from __future__ import annotations

import time
from typing import Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.db.redis import redis_client


class RedisRateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limit_per_minute: int | None = None):
        super().__init__(app)
        self.limit_per_minute = limit_per_minute or settings.rate_limit_per_minute

    @staticmethod
    def _resolve_subject(request: Request) -> str:
        """
        Prefer stable user identity when available.
        This avoids grouping all WordPress-proxied traffic under one server IP.
        """
        wp_user_id = (request.headers.get("x-wp-user-id") or "").strip()
        if wp_user_id:
            return f"wp:{wp_user_id}"

        auth = (request.headers.get("authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
            if token:
                # Do not parse claims here; just isolate per presented token.
                return f"jwt:{token}"

        forwarded = (request.headers.get("x-forwarded-for") or "").strip()
        if forwarded:
            return f"ip:{forwarded.split(',')[0].strip()}"

        real_ip = (request.headers.get("x-real-ip") or "").strip()
        if real_ip:
            return f"ip:{real_ip}"

        ip = request.client.host if request.client else "unknown"
        return f"ip:{ip}"

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable]):
        if request.url.path.startswith("/health") or request.url.path.startswith("/metrics"):
            return await call_next(request)

        subject = self._resolve_subject(request)
        minute_bucket = int(time.time() // 60)
        key = f"rl:{subject}:{minute_bucket}"

        try:
            count = await redis_client.incr(key)
            if count == 1:
                await redis_client.expire(key, 65)
            if count > self.limit_per_minute:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded"},
                    headers={"Retry-After": "60"},
                )
        except Exception:
            # Fail-open when Redis is unavailable.
            pass

        return await call_next(request)
