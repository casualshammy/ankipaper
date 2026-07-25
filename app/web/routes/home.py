"""Главная страница: список колод со статистикой."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import __version__
from app.web.deps import get_session
from app.web.session import Session

router = APIRouter()


@router.get("/", response_model=None)
async def home(
    request: Request,
    sync_ok: str | None = None,
    sync_error: str | None = None,
    session: Session = Depends(get_session),
) -> HTMLResponse | RedirectResponse:
    """Отображает список колод или редиректит на /login."""

    if not session.is_authenticated:
        return RedirectResponse("/login", status_code=303)

    settings = request.app.state.settings
    templates: Jinja2Templates = request.app.state.templates

    from app.storage import get_collection_manager
    from app.domain.scheduler import list_deck_stats

    manager = get_collection_manager()
    has_collection = manager.has_collection()
    decks: list = []
    error: str | None = None

    if has_collection:
        try:
            decks = await manager.run(list_deck_stats)
        except Exception as exc:  # noqa: BLE001
            error = f"Failed to read deck stats: {exc}"

    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "version": __version__,
            "base_url": settings.base_url,
            "decks": decks,
            "has_collection": has_collection,
            "error": error,
            "sync_ok": sync_ok,
            "sync_error": sync_error,
        },
    )