from __future__ import annotations

import time
from logging import Logger

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.config import Settings
from app.web.ratelimit import client_ip
from app.web.session import read_session


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Replace uvicorn's built-in access log."""

    def __init__(self, app: ASGIApp, settings: Settings, logger: Logger):
        super().__init__(app)
        self.settings = settings
        self.logger = logger

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
      start = time.monotonic()
      response = await call_next(request)
      duration_ms = (time.monotonic() - start) * 1000.0
      ip = client_ip(request, self.settings)
      query = f"?{request.url.query}" if request.url.query else ""
      user = "-"
      session = read_session(request)
      if session.is_authenticated and session.account_id is not None:
          user = session.account_id
      self.logger.info(
          '%s [%s] <<- "%s %s%s" %d %.2fms',
          ip,
          user,
          request.method,
          request.url.path,
          query,
          response.status_code,
          duration_ms,
      )
      if self.settings.debug_headers:
          self.logger.info("headers for %s %s:", request.method, request.url.path)
          for name, value in request.headers.items():
              self.logger.info("  %s: %s", name, value)
      return response
