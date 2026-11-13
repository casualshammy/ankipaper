"""Home page route (deck list)."""

from __future__ import annotations

import asyncio
import logging
import time
from math import ceil

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.domain.scheduler import DeckStats, list_deck_stats
from app.storage.account import Account
from app.sync.client import is_sync_required_or_throw
from app.toolkit.account_toolkit import get_account_logger
from app.web.deps import get_current_account_optional

_common_logger = logging.getLogger(__name__)
_deck_stats_inflight: dict[str, asyncio.Task[list[DeckStats]]] = {}
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
    """Renders the landing page (anonymous) or the list of decks."""

    templates: Jinja2Templates = request.app.state.templates

    if account is None:
        return templates.TemplateResponse(
            request,
            "landing.html")

    logger = get_account_logger(_common_logger, account.id)
    has_collection = account.deck_collection_file_exists()
    collection_was_open = account.is_open() if has_collection else False
    decks: list[DeckStats] = []
    error: str | None = None

    t_total_start = time.monotonic()
    t_decks_start = t_total_start

    if has_collection:
        inflight = _deck_stats_inflight.get(account.id)
        if inflight is None or inflight.done():
            inflight = asyncio.create_task(account.run(list_deck_stats))
            _deck_stats_inflight[account.id] = inflight

        max_task_time_sec = 2
        try:
            await asyncio.wait_for(asyncio.shield(inflight), timeout=max_task_time_sec)
            _deck_stats_inflight.pop(account.id, None)
            decks = inflight.result()
        except TimeoutError:
            logger.warning(f"home loading page: deck-stats fetch exceeded {max_task_time_sec}s") 
            return templates.TemplateResponse(
                request,
                "home_loading.html",
                {
                    "account": account,
                    "refresh_interval_seconds": ceil(max_task_time_sec),
                })
        except Exception as exc:
            _deck_stats_inflight.pop(account.id, None)
            error = "Failed to read deck stats!"
            logger.error(f"Failed to read deck stats: {exc}")

    t_decks_ms = (time.monotonic() - t_decks_start) * 1000.0

    t_sync_start = time.monotonic()
    is_sync_required = await _is_sync_required(logger, account)
    t_sync_ms = (time.monotonic() - t_sync_start) * 1000.0

    t_render_start = time.monotonic()
    response = templates.TemplateResponse(
        request,
        "home.html",
        {
            "decks": decks,
            "has_collection": has_collection,
            "error": error,
            "sync_ok": sync_ok,
            "sync_error": sync_error,
            "rebuild_ok": rebuild_ok,
            "empty_ok": empty_ok,
            "sync_required": is_sync_required,
        },
    )
    t_render_ms = (time.monotonic() - t_render_start) * 1000.0
    t_total_ms = (time.monotonic() - t_total_start) * 1000.0

    logger.info(
        "home timing: total=%.1fms decks=%.1fms sync_probe=%.1fms render=%.1fms "
        "collection_was_open_before=%s collection_is_open_after=%s decks_count=%d sync_required=%s",
        t_total_ms,
        t_decks_ms,
        t_sync_ms,
        t_render_ms,
        collection_was_open,
        account.is_open(),
        len(decks),
        is_sync_required,
    )
    return response


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
