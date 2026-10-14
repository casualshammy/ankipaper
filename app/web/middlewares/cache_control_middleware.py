from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class CacheControlMiddleware(BaseHTTPMiddleware):
  """Middleware to add cache control headers to HTML responses."""

  async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
    response = await call_next(request)

    if (response.status_code != 200):
      return response

    content_type = response.headers.get("Content-Type", "")
    if (content_type.find("text/html") == -1):
      return response

    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response