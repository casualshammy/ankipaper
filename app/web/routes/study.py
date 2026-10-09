"""Card review routes."""

from __future__ import annotations

import asyncio
import logging
import time
from math import ceil
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.domain.scheduler import (
    CardIntervals,
    DeckStats,
    Rating,
    answer_card,
    card_deck_matches_or_descends,
    delete_note_by_card,
    empty_filtered_deck,
    get_card_view,
    get_deck_card_count,
    get_deck_due_breakdown,
    get_next_card,
    get_undo_status,
    list_deck_stats,
    rebuild_filtered_deck,
    set_card_flag,
    set_card_marked,
    undo_last_op,
)
from app.storage.account import Account
from app.sync.client import is_sync_required_or_throw
from app.web.csrf import require_csrf
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
    rebuild_error: str | None = None,
    empty_ok: int | None = None,
    empty_error: str | None = None,
    account: Account | None = Depends(get_current_account_optional),
) -> HTMLResponse | RedirectResponse:
    """Renders the landing page (anonymous) or the list of decks."""

    settings = request.app.state.settings
    templates: Jinja2Templates = request.app.state.templates

    if account is None:
        return templates.TemplateResponse(
            request,
            "landing.html")

    logger = _common_logger.getChild(account.id)
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
            "account": account,
            "decks": decks,
            "has_collection": has_collection,
            "error": error,
            "sync_ok": sync_ok,
            "sync_error": sync_error,
            "rebuild_ok": rebuild_ok,
            "rebuild_error": rebuild_error,
            "empty_ok": empty_ok,
            "empty_error": empty_error,
            "media_collection_too_large": account.sync_state.media_collection_too_large,
            "media_max_collection_bytes": settings.media_max_collection_bytes,
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


@router.post("/deck/{deck_id}/rebuild", response_model=None)
async def deck_rebuild_post(
    deck_id: int,
    account: Account | None = Depends(get_current_account_optional),
    _: None = Depends(require_csrf),
) -> RedirectResponse:
    """Rebuilds a filtered deck using its search terms."""

    if account is None:
        return RedirectResponse("/login", status_code=303)

    logger = _common_logger.getChild(account.id)

    try:
        count = await account.run(rebuild_filtered_deck, deck_id)
    except Exception as exc:
        logger.warning("rebuild_filtered_deck failed: deck_id=%s err=%s", deck_id, exc)
        return RedirectResponse(
            f"/?rebuild_error={exc}", status_code=303
        )
    logger.info("rebuild_filtered_deck: deck_id=%s count=%s", deck_id, count)
    return RedirectResponse(f"/?rebuild_ok={count}", status_code=303)


@router.post("/deck/{deck_id}/empty", response_model=None)
async def deck_empty_post(
    deck_id: int,
    account: Account | None = Depends(get_current_account_optional),
    _: None = Depends(require_csrf),
) -> RedirectResponse:
    """Returns all cards from a filtered deck to their home decks."""

    if account is None:
        return RedirectResponse("/login", status_code=303)

    logger = _common_logger.getChild(account.id)

    try:
        count = await account.run(empty_filtered_deck, deck_id)
    except Exception as exc:
        logger.warning("empty_filtered_deck failed: deck_id=%s err=%s", deck_id, exc)
        return RedirectResponse(f"/?empty_error={exc}", status_code=303)
    logger.info("empty_filtered_deck: deck_id=%s count=%s", deck_id, count)
    return RedirectResponse(f"/?empty_ok={count}", status_code=303)


@router.get("/deck/{deck_id}/study", response_model=None)
async def study_get(
    request: Request,
    deck_id: int,
    error_msg: str = Query(""),
    account: Account | None = Depends(get_current_account_optional),
) -> HTMLResponse | RedirectResponse:
    """Shows the front of the next due card or the session-complete page."""

    if account is None:
        return RedirectResponse("/login", status_code=303)

    templates: Jinja2Templates = request.app.state.templates

    view = await account.run(get_next_card, deck_id)
    if view is None:
        return await _session_done(request, account, deck_id)

    logger = _common_logger.getChild(account.id)

    breakdown = await account.run(get_deck_due_breakdown, deck_id)
    is_filtered = await account.run(_deck_is_filtered, deck_id)
    has_cards = bool(await account.run(get_deck_card_count, deck_id))
    undo = await account.run(get_undo_status)
    is_sync_required = await _is_sync_required(logger, account)
    return templates.TemplateResponse(
        request,
        "study_front.html",
        {
            "account": account,
            "deck_id": deck_id,
            "is_filtered": is_filtered,
            "has_cards": has_cards,
            "card": view,
            "remaining": breakdown.total,
            "remaining_new": breakdown.new,
            "remaining_learning": breakdown.learning,
            "remaining_review": breakdown.review,
            "undo": undo,
            "error_msg": error_msg,
            "sync_required": is_sync_required
        },
    )


@router.post("/deck/{deck_id}/study", response_model=None)
async def study_post(
    request: Request,
    deck_id: int,
    reveal: str = Form(""),
    rate: str = Form(""),
    card_id: str = Form(""),
    card_type: str = Form(""),
    again_interval: str = Form(""),
    hard_interval: str = Form(""),
    good_interval: str = Form(""),
    easy_interval: str = Form(""),
    account: Account | None = Depends(get_current_account_optional),
    _: None = Depends(require_csrf),
) -> HTMLResponse | RedirectResponse:
    """Handles Reveal / Answer within a review session."""

    if account is None:
        return RedirectResponse("/login", status_code=303)

    templates: Jinja2Templates = request.app.state.templates

    if not card_id:
        return RedirectResponse(f"/deck/{deck_id}/study", status_code=303)

    try:
        card_id_int = int(card_id)
    except ValueError:
        return RedirectResponse(f"/deck/{deck_id}/study", status_code=303)

    logger = _common_logger.getChild(account.id)

    if reveal:
        intervals = CardIntervals(
            again=again_interval or "—",
            hard=hard_interval or "—",
            good=good_interval or "—",
            easy=easy_interval or "—",
        )
        view = await account.run(
            get_card_view, card_id_int, card_type or "new", intervals
        )
        if view is None:
            return RedirectResponse(f"/deck/{deck_id}/study", status_code=303)

        breakdown = await account.run(get_deck_due_breakdown, deck_id)
        is_filtered = await account.run(_deck_is_filtered, deck_id)
        is_sync_required = await _is_sync_required(logger, account)
        return templates.TemplateResponse(
            request,
            "study_back.html",
            {
                "account": account,
                "deck_id": deck_id,
                "is_filtered": is_filtered,
                "card": view,
                "remaining": breakdown.total,
                "remaining_new": breakdown.new,
                "remaining_learning": breakdown.learning,
                "remaining_review": breakdown.review,
                "sync_required": is_sync_required
            },
        )

    if rate:
        try:
            rating = Rating(int(rate))
        except ValueError:
            return RedirectResponse(f"/deck/{deck_id}/study", status_code=303)

        outcome = await account.run(answer_card, card_id_int, rating, deck_id=deck_id)
        if outcome.stale:
            logger.info(
                "study_post: stale answer card_id=%s deck_id=%s rating=%s; "
                "redirecting to current head",
                card_id_int,
                deck_id,
                int(rating),
            )
            return RedirectResponse(f"/deck/{deck_id}/study", status_code=303)
        if outcome.next_card_id is None:
            return await _session_done(request, account, deck_id)
        return RedirectResponse(f"/deck/{deck_id}/study", status_code=303)

    return RedirectResponse(f"/deck/{deck_id}/study", status_code=303)


@router.post("/deck/{deck_id}/undo", response_model=None)
async def undo_post(
    deck_id: int,
    account: Account | None = Depends(get_current_account_optional),
    _: None = Depends(require_csrf),
) -> RedirectResponse:
    """Undo the last review action and return to the study page."""

    if account is None:
        return RedirectResponse("/login", status_code=303)

    await account.run(undo_last_op)
    return RedirectResponse(f"/deck/{deck_id}/study", status_code=303)


@router.get("/deck/{deck_id}/delete-note", response_model=None)
async def delete_note_get(
    request: Request,
    deck_id: int,
    card_id: str = Query(""),
    account: Account | None = Depends(get_current_account_optional),
) -> HTMLResponse | RedirectResponse:
    """Render the delete-confirmation page for the given card."""

    if account is None:
        return RedirectResponse("/login", status_code=303)

    logger = _common_logger.getChild(account.id)

    try:
        cid = int(card_id)
    except ValueError:
        card_id_truncated = card_id[:100]
        logger.warning(f"delete_note_get: can't parse card_id: '{card_id_truncated}'")
        err_msg = f"Card id '{card_id_truncated}' is not a valid number"
        return RedirectResponse(f"/deck/{deck_id}/study?error_msg={quote(err_msg, safe='')}", status_code=303)

    view = await account.run(get_card_view, cid, "new", None)
    if view is None:
        err_msg = f"Card '{cid}' does not exist"
        return RedirectResponse(f"/deck/{deck_id}/study?error_msg={quote(err_msg, safe='')}", status_code=303)

    true_deck = await account.run(card_deck_matches_or_descends, view.deck_id, deck_id)
    if (not true_deck):
        err = f"delete_note_get: invalid deck id '{deck_id}', card's deck id: '{view.deck_id}'"
        logger.warning(err)
        err_msg = f"Card '{cid}' does not belong to the current deck '{deck_id}'"
        return RedirectResponse(f"/deck/{deck_id}/study?error_msg={quote(err_msg, safe='')}", status_code=303)

    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "delete_card_confirm.html",
        {
            "account": account,
            "deck_id": deck_id,
            "card": view,
        },
    )


@router.post("/deck/{deck_id}/delete-note", response_model=None)
async def delete_note_post(
    deck_id: int,
    card_id: str = Form(""),
    account: Account | None = Depends(get_current_account_optional),
    _: None = Depends(require_csrf),
) -> RedirectResponse:
    """Delete the note behind the card and return to the study page."""

    if account is None:
        return RedirectResponse("/login", status_code=303)

    logger = _common_logger.getChild(account.id)

    try:
        cid = int(card_id)
        await account.run(delete_note_by_card, deck_id, cid)
        logger.info("delete_note_post: card_id=%s deck_id=%s deleted", cid, deck_id)
        return RedirectResponse(f"/deck/{deck_id}/study", status_code=303)
    except ValueError as exc:
        exc_str = str(exc)
        logger.warning(
            "delete_note_post: card_id=%s deck_id=%s failed: %s",
            card_id[:100],
            deck_id,
            exc_str,
        )
        return RedirectResponse(
            f"/deck/{deck_id}/study?error_msg={quote(exc_str, safe='')}",
            status_code=303,
        )


@router.post("/set-card-flag", response_model=None)
async def flag_post(
    payload: dict = Body(default_factory=dict),
    account: Account | None = Depends(get_current_account_optional),
    _: None = Depends(require_csrf),
) -> Response:
    """Sets the user flag (0..4) on a card."""

    if account is None:
        return Response(status_code=401)

    logger = _common_logger.getChild(account.id)

    card_id = payload.get("card_id", "")
    flag = payload.get("flag", "")

    if type(card_id) is not int:
        logger.warning("set-card-flag: invalid card_id=%s", str(card_id)[:100])
        return Response(status_code=400, content = "invalid card_id")
    if type(flag) is not int:
        logger.warning("set-card-flag: invalid flag=%s", str(flag)[:100])
        return Response(status_code=400, content = "invalid flag")

    try:
        await account.run(set_card_flag, card_id, flag)
        return Response(status_code=204)
    except ValueError as exc:
        exc_str = str(exc)
        logger.warning("set-card-flag: %s", exc_str[:100])
        return Response(status_code=400, content = exc_str)

        
@router.post("/set-card-mark", response_model=None)
async def mark_post(
    payload: dict = Body(default_factory=dict),
    account: Account | None = Depends(get_current_account_optional),
    _: None = Depends(require_csrf),
) -> Response:
    """Sets the "marked" (star) state on a card."""

    if account is None:
        return Response(status_code=401)

    card_id = payload.get("card_id", "")
    marked = payload.get("marked", "")

    logger = _common_logger.getChild(account.id)

    if type(card_id) is not int:
        logger.warning("set-card-mark: invalid card_id=%s", str(card_id)[:100])
        return Response(status_code=400, content = "invalid card_id")
    if type(marked) is not bool:
        logger.warning("set-card-mark: invalid marked=%s", str(marked)[:100])
        return Response(status_code=400, content = "invalid marked")
    
    try:
        await account.run(set_card_marked, card_id, marked)
        return Response(status_code=204)
    except ValueError as exc:
        exc_str= str(exc)
        logger.warning("set-card-mark: %s", exc_str[:100])
        return Response(status_code=400, content = exc_str)


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


async def _session_done(
    request: Request,
    account: Account,
    deck_id: int,
) -> HTMLResponse:
    """Renders the session-complete page."""

    logger = _common_logger.getChild(account.id)

    templates: Jinja2Templates = request.app.state.templates
    is_filtered = await account.run(_deck_is_filtered, deck_id)
    has_cards = bool(await account.run(get_deck_card_count, deck_id))
    undo = await account.run(get_undo_status)
    is_sync_required = await _is_sync_required(logger, account)
    return templates.TemplateResponse(
        request,
        "study_done.html",
        {
            "account": account,
            "deck_id": deck_id,
            "is_filtered": is_filtered,
            "has_cards": has_cards,
            "remaining": 0,
            "remaining_new": 0,
            "remaining_learning": 0,
            "remaining_review": 0,
            "undo": undo,
            "sync_required": is_sync_required
        },
    )


def _deck_is_filtered(col, deck_id: int) -> bool:
    """True if the deck is a filtered (cram) deck."""

    return bool(col.decks.is_filtered(int(deck_id)))
