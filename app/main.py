"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __version__
from app.config import Settings, get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application.

    Args:
        settings: optional pre-built Settings instance (used by tests).
    """

    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        # Close the Redis client used by the login rate limiter so the
        # connection pool does not leak across worker shutdowns.
        from app.web.ratelimit import close_redis

        await close_redis()

    app = FastAPI(
        title="kindlanki",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    app.state.settings = settings
    app.state.templates = Jinja2Templates(
        directory=str(BASE_DIR / "web" / "templates"),
    )
    # CSRF token generator is exposed to every template via the
    # ``csrf_token(request)`` callable — see ``app/web/csrf.py``.
    from app.web.csrf import csrf_token as csrf_token_global

    app.state.templates.env.globals["csrf_token"] = csrf_token_global

    app.mount(
        "/static",
        StaticFiles(directory=BASE_DIR / "web" / "static"),
        name="static",
    )

    from app.web.routes import auth as auth_routes
    from app.web.routes import home as home_routes
    from app.web.routes import media as media_routes
    from app.web.routes import study as study_routes
    from app.web.routes import sync as sync_routes

    app.include_router(auth_routes.router)
    app.include_router(home_routes.router)
    app.include_router(media_routes.router)
    app.include_router(sync_routes.router)
    app.include_router(study_routes.router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> JSONResponse:
        """Health probe used by docker healthcheck."""

        return JSONResponse({"status": "ok", "version": __version__})

    return app


app = create_app()
