"""Home page route (deck list)."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app import __version__
from app.domain.scheduler import DeckStats, list_deck_stats, set_deck_collapsed
from app.storage.account import Account
from app.sync.client import is_sync_required_or_throw
from app.toolkit.account_toolkit import get_account_logger
from app.web.csrf import require_csrf
from app.web.deps import get_current_account_optional
from app.web.req_types import DeckCollapseReq

_common_logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/", response_model=None)
async def home(
    request: Request,
    sync_ok: str | None = None,
    sync_error: str | None = None,
    rebuild_ok: int | None = None,
    empty_ok: int | None = None,
    account: Account | None = Depends(get_current_account_optional),
) -> HTMLResponse:
    """Renders the landing page (anonymous) or the deck-list shell.

    The shell is served immediately — it never opens the collection and
    never talks to AnkiWeb. The deck list arrives separately via
    ``GET /decks`` and is inserted by the client-side script.
    """

    templates: Jinja2Templates = request.app.state.templates

    if account is None:
        return templates.TemplateResponse(
            request,
            "landing.html")

    has_collection = account.deck_collection_file_exists()

    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "has_collection": has_collection,
            "sync_ok": sync_ok,
            "sync_error": sync_error,
            "rebuild_ok": rebuild_ok,
            "empty_ok": empty_ok,
            "bundle_v": __version__,
        },
    )


@router.get("/decks", response_model=None)
async def decks_fragment_get(
    request: Request,
    account: Account | None = Depends(get_current_account_optional),
) -> HTMLResponse | JSONResponse:
    """Returns the deck tree as an HTML fragment for the home page."""

    if account is None:
        return JSONResponse({"redirect": "/login"}, status_code=401)

    logger = get_account_logger(_common_logger, account.id)
    templates: Jinja2Templates = request.app.state.templates

    if not account.deck_collection_file_exists():
        return templates.TemplateResponse(
            request,
            "home_decks_fragment.html",
            {
                "decks": [],
                "error": "No collection yet. Press Sync to download your decks.",
            },
        )

    t_start = time.monotonic()
    decks: list[DeckStats] = []
    error: str | None = None

    try:
        decks = await account.run(list_deck_stats)
    except Exception as exc:
        error = "Failed to read deck stats!"
        logger.error(f"Failed to read deck stats: {exc}")

    t_total_ms = (time.monotonic() - t_start) * 1000.0
    logger.info(f"decks fragment: total={t_total_ms:.1f}ms decks_count={len(decks)}")

    return templates.TemplateResponse(
        request,
        "home_decks_fragment.html",
        {
            "decks": decks,
            "error": error,
        },
    )


@router.get("/sync-status", response_model=None)
async def sync_status_get(
    account: Account | None = Depends(get_current_account_optional),
) -> JSONResponse:
    """Tells the client whether AnkiWeb has changes we have not pulled yet."""

    if account is None:
        return JSONResponse({"redirect": "/login"}, status_code=401)

    logger = get_account_logger(_common_logger, account.id)
    is_sync_required = await _is_sync_required(logger, account)
    return JSONResponse({"sync_required": is_sync_required})


@router.post("/deck/{deck_id}/collapse", response_model=None)
async def deck_collapse_post(
    deck_id: int,
    payload: DeckCollapseReq,
    account: Account | None = Depends(get_current_account_optional),
    _: None = Depends(require_csrf),
) -> JSONResponse:
    """Collapses or expands a deck in the deck tree."""

    if account is None:
        return JSONResponse({"redirect": "/login"}, status_code=401)

    if not account.deck_collection_file_exists():
        return JSONResponse({"error_msg": "No collection."}, status_code=404)

    logger = get_account_logger(_common_logger, account.id)
    try:
        await account.run(set_deck_collapsed, deck_id, payload.collapsed)
    except ValueError as exc:
        logger.warning("deck_collapse: %s", exc)
        return JSONResponse({"error_msg": str(exc)}, status_code=400)

    logger.info("deck_collapse: deck_id=%s collapsed=%s", deck_id, payload.collapsed)
    return JSONResponse({"ok": True, "collapsed": payload.collapsed})


async def _is_sync_required(
    logger: logging.Logger,
    account: Account,
) -> bool:
    if account.sync_state.status == "running":
        return False

    try:
        result, _ = await is_sync_required_or_throw(account, account.sync_state.conflict_new_endpoint)
        return result
    except Exception as ex:
        logger.error(f"_is_sync_required failed: {ex}")
        return True
