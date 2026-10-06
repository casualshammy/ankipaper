"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from prometheus_client import REGISTRY

from app import __version__
from app.config import Settings, get_settings
from app.metrics.collectors import AnkiPaperCollector
from app.storage.account import get_account_store
from app.web.middlewares import AccessLogMiddleware, MetricsMiddleware

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

    REGISTRY.register(AnkiPaperCollector())

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        from app.storage.eviction import idle_collection_sweeper
        from app.web.ratelimit import close_redis

        store = get_account_store()
        sweeper_task = asyncio.create_task(idle_collection_sweeper(store))
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
    
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(AccessLogMiddleware, settings, logger)

    # CSRF token generator is exposed to every template via the
    # ``csrf_token(request)`` callable — see ``app/web/csrf.py``.
    from app.web.csrf import csrf_token as csrf_token_global

    app.state.templates.env.globals["version"] = __version__
    app.state.templates.env.globals["csrf_token"] = csrf_token_global
    app.state.templates.env.globals["show_privacy_policy"] = settings.show_privacy_policy
    app.state.templates.env.globals["contacts_email"] = settings.contacts_email

    from app.web.seo import canonical_url, og_image_url, webapplication_jsonld

    app.state.templates.env.globals["brand_name"] = settings.brand_name
    app.state.templates.env.globals["default_description"] = settings.meta_description
    app.state.templates.env.globals["canonical_url"] = canonical_url
    app.state.templates.env.globals["og_image_url"] = og_image_url
    app.state.templates.env.globals["webapplication_jsonld"] = webapplication_jsonld

    from app.web.routes import auth as auth_routes
    from app.web.routes import media as media_routes
    from app.web.routes import metrics as metrics_router
    from app.web.routes import seo as seo_routes
    from app.web.routes import static as static_routes
    from app.web.routes import study as study_routes
    from app.web.routes import sync as sync_routes

    app.include_router(auth_routes.router)
    app.include_router(media_routes.router)
    app.include_router(metrics_router.router)
    app.include_router(seo_routes.router)
    app.include_router(static_routes.router)
    app.include_router(sync_routes.router)
    app.include_router(study_routes.router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> JSONResponse:
        """Health probe used by docker healthcheck."""

        return JSONResponse({"status": "ok", "version": __version__})

    @app.head("/", include_in_schema=False)
    async def home_head() -> Response:
        """Lightweight probe: the server is up."""

        return Response(status_code=200)

    return app


app = create_app()
