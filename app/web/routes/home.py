"""Home page: list of decks with statistics."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import __version__
from app.domain.scheduler import list_deck_stats, rebuild_filtered_deck
from app.storage.account import Account
from app.web.csrf import require_csrf
from app.web.deps import get_current_account_optional, get_session
from app.web.session import Session

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/", response_model=None)
async def home(
    request: Request,
    sync_ok: str | None = None,
    sync_error: str | None = None,
    rebuild_ok: int | None = None,
    rebuild_error: str | None = None,
    session: Session = Depends(get_session),
    account: Account | None = Depends(get_current_account_optional),
) -> HTMLResponse | RedirectResponse:
    """Renders the landing page (anonymous) or the list of decks."""

    settings = request.app.state.settings
    templates: Jinja2Templates = request.app.state.templates

    if account is None:
        return templates.TemplateResponse(
            request,
            "landing.html",
            {"version": __version__},
        )

    manager = account.manager
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
            "account": account,
            "decks": decks,
            "has_collection": has_collection,
            "error": error,
            "sync_ok": sync_ok,
            "sync_error": sync_error,
            "rebuild_ok": rebuild_ok,
            "rebuild_error": rebuild_error,
            "media_collection_too_large": account.sync_state.media_collection_too_large,
            "media_max_collection_bytes": settings.media_max_collection_bytes,
        },
    )


@router.post("/deck/{deck_id}/rebuild", response_model=None)
async def deck_rebuild_post(
    deck_id: int,
    account: Account | None = Depends(get_current_account_optional),
    _: None = Depends(require_csrf),
) -> RedirectResponse:
    """Rebuilds a filtered deck using its search terms."""

    if account is None:
        return RedirectResponse("/login", status_code=303)

    try:
        count = await account.manager.run(rebuild_filtered_deck, deck_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("rebuild_filtered_deck failed: deck_id=%s err=%s", deck_id, exc)
        return RedirectResponse(
            f"/?rebuild_error={exc}", status_code=303
        )
    logger.info("rebuild_filtered_deck: deck_id=%s count=%s", deck_id, count)
    return RedirectResponse(f"/?rebuild_ok={count}", status_code=303)
