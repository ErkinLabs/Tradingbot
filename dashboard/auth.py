"""Optional API-key auth for dashboard /api routes."""

from __future__ import annotations

from fastapi import Header, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

import config


def verify_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    """FastAPI dependency — skips check when DASHBOARD_API_KEY is unset."""
    if not config.DASHBOARD_API_KEY:
        return
    if x_api_key != config.DASHBOARD_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


class DashboardAuthMiddleware(BaseHTTPMiddleware):
    """Reject unauthenticated /api/* requests when DASHBOARD_API_KEY is configured."""

    async def dispatch(self, request: Request, call_next):
        if not config.DASHBOARD_API_KEY:
            return await call_next(request)

        path = request.url.path
        if path.startswith("/api/"):
            key = request.headers.get("X-API-Key", "")
            if key != config.DASHBOARD_API_KEY:
                return JSONResponse({"detail": "Invalid or missing API key"}, status_code=401)

        return await call_next(request)
