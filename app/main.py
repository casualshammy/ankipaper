"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from app import __version__
from app.config import Settings, get_settings
from app.storage.account import get_account_store
from app.web.ratelimit import client_ip
from app.web.session import read_session

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
        from app.storage.eviction import _idle_collection_sweeper
        from app.web.ratelimit import close_redis

        store = get_account_store()
        sweeper_task = asyncio.create_task(_idle_collection_sweeper(store))
        try:
            yield
        finally:
            sweeper_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweeper_task

            await close_redis()

    app = FastAPI(
        title="AnkiPaper",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    app.state.settings = settings
    app.state.templates = Jinja2Templates(
        directory=str(BASE_DIR / "web" / "templates"),
    )

    # Replace uvicorn's built-in access log
    @app.middleware("http")
    async def access_log_middleware(request: Request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000.0
        ip = client_ip(request, settings)
        query = f"?{request.url.query}" if request.url.query else ""
        user = "-"
        session = read_session(request)
        if session.is_authenticated and session.account_id is not None:
            user = session.account_id
        logger.info(
            '%s [%s] <<- "%s %s%s" %d %.2fms',
            ip,
            user,
            request.method,
            request.url.path,
            query,
            response.status_code,
            duration_ms,
        )
        if settings.debug_headers:
            logger.info("headers for %s %s:", request.method, request.url.path)
            for name, value in request.headers.items():
                logger.info("  %s: %s", name, value)
        return response

    # CSRF token generator is exposed to every template via the
    # ``csrf_token(request)`` callable — see ``app/web/csrf.py``.
    from app.web.csrf import csrf_token as csrf_token_global

    app.state.templates.env.globals["csrf_token"] = csrf_token_global
    app.state.templates.env.globals["show_privacy_policy"] = (
        settings.show_privacy_policy
    )

    from app.web.seo import canonical_url, og_image_url, webapplication_jsonld

    app.state.templates.env.globals["brand_name"] = settings.brand_name
    app.state.templates.env.globals["default_description"] = settings.meta_description
    app.state.templates.env.globals["canonical_url"] = canonical_url
    app.state.templates.env.globals["og_image_url"] = og_image_url
    app.state.templates.env.globals["webapplication_jsonld"] = webapplication_jsonld

    from app.web.routes import auth as auth_routes
    from app.web.routes import home as home_routes
    from app.web.routes import media as media_routes
    from app.web.routes import seo as seo_routes
    from app.web.routes import static as static_routes
    from app.web.routes import study as study_routes
    from app.web.routes import sync as sync_routes

    app.include_router(auth_routes.router)
    app.include_router(home_routes.router)
    app.include_router(media_routes.router)
    app.include_router(seo_routes.router)
    app.include_router(static_routes.router)
    app.include_router(sync_routes.router)
    app.include_router(study_routes.router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> JSONResponse:
        """Health probe used by docker healthcheck."""

        return JSONResponse({"status": "ok", "version": __version__})

    return app


app = create_app()
