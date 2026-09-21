"""Records request count, latency and in-progress gauge per route template. 
Paths under ``/healthz`` are excluded to keep healthcheck / static-asset / media 
traffic out of the dashboards.
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.metrics.definitions import HTTP_REQUEST_DURATION, HTTP_REQUESTS

_EXCLUDED_PREFIXES: frozenset[str] = frozenset("/healthz")
_EXCLUDED_EXACT: frozenset[str] = frozenset({"/favicon.ico"})


class MetricsMiddleware(BaseHTTPMiddleware):
    """Records HTTP request count and latency per route template."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not self._should_measure(path):
            return await call_next(request)

        method = request.method
        start = time.monotonic()

        response = await call_next(request)

        route = self._get_route_label(request)
        HTTP_REQUEST_DURATION.labels(route=route).observe(time.monotonic() - start)
        HTTP_REQUESTS.labels(
            method=method,
            route=route,
            status_class=self._status_class(response.status_code),
        ).inc()
        return response

    @staticmethod
    def _should_measure(path: str) -> bool:
        if path in _EXCLUDED_EXACT:
            return False
        return not any(path.startswith(p) for p in _EXCLUDED_PREFIXES)

    @staticmethod
    def _get_route_label(request: Request) -> str:
        """Returns the route template, or a synthetic bucket for unknown routes.

        ``request.scope["route"]`` is populated by the router only AFTER
        routing has happened, so this must be called after ``call_next``.
        """

        route = request.scope.get("route")
        if route is not None and getattr(route, "path", None):
            return route.path
        return "__not_found__"

    @staticmethod
    def _status_class(status_code: int) -> str:
        """Returns the HTTP status class label, e.g. ``"2xx"``."""

        return f"{status_code // 100}xx"