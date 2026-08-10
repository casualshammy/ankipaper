"""Card review routes."""

from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app import __version__
from app.domain.scheduler import (
    CardIntervals,
    Rating,
    answer_card,
    card_deck_matches_or_descends,
    delete_note_by_card,
    get_card_view,
    get_deck_card_count,
    get_deck_due_breakdown,
    get_next_card,
    get_undo_status,
    set_card_flag,
    set_card_marked,
    undo_last_op,
)
from app.storage.account import Account
from app.sync.client import try_sync
from app.web.csrf import require_csrf
from app.web.deps import get_current_account_optional

logger = logging.getLogger(__name__)

router = APIRouter()


async def _session_done(
    request: Request,
    account: Account,
    deck_id: int,
) -> HTMLResponse:
    """Renders the session-complete page."""

    templates: Jinja2Templates = request.app.state.templates
    is_filtered = await account.manager.run(_deck_is_filtered, deck_id)
    has_cards = bool(await account.manager.run(get_deck_card_count, deck_id))
    undo = await account.manager.run(get_undo_status)
    return templates.TemplateResponse(
        request,
        "study_done.html",
        {
            "version": __version__,
            "account": account,
            "deck_id": deck_id,
            "is_filtered": is_filtered,
            "has_cards": has_cards,
            "remaining": 0,
            "remaining_new": 0,
            "remaining_learning": 0,
            "remaining_review": 0,
            "undo": undo,
        },
    )


def _deck_is_filtered(col, deck_id: int) -> bool:
    """True if the deck is a filtered (cram) deck."""

    return bool(col.decks.is_filtered(int(deck_id)))


async def _auto_sync_if_possible(account: Account) -> tuple[bool, str | None, bool]:
    """Best-effort sync after the review session is finished."""

    host_key = account.host_key()
    if not host_key:
        return False, None, False

    result = await account.manager.run(try_sync, host_key)
    return (result.required, result.error, True)


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
    manager = account.manager

    view = await manager.run(get_next_card, deck_id)
    if view is None:
        return await _session_done(request, account, deck_id)

    breakdown = await manager.run(get_deck_due_breakdown, deck_id)
    is_filtered = await manager.run(_deck_is_filtered, deck_id)
    has_cards = bool(await manager.run(get_deck_card_count, deck_id))
    undo = await manager.run(get_undo_status)
    return templates.TemplateResponse(
        request,
        "study_front.html",
        {
            "version": __version__,
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
    manager = account.manager

    if not card_id:
        return RedirectResponse(f"/deck/{deck_id}/study", status_code=303)

    try:
        card_id_int = int(card_id)
    except ValueError:
        return RedirectResponse(f"/deck/{deck_id}/study", status_code=303)

    if reveal:
        intervals = CardIntervals(
            again=again_interval or "—",
            hard=hard_interval or "—",
            good=good_interval or "—",
            easy=easy_interval or "—",
        )
        view = await manager.run(
            get_card_view, card_id_int, card_type or "new", intervals
        )
        if view is None:
            return RedirectResponse(f"/deck/{deck_id}/study", status_code=303)

        breakdown = await manager.run(get_deck_due_breakdown, deck_id)
        is_filtered = await manager.run(_deck_is_filtered, deck_id)
        return templates.TemplateResponse(
            request,
            "study_back.html",
            {
                "version": __version__,
                "account": account,
                "deck_id": deck_id,
                "is_filtered": is_filtered,
                "card": view,
                "remaining": breakdown.total,
                "remaining_new": breakdown.new,
                "remaining_learning": breakdown.learning,
                "remaining_review": breakdown.review,
            },
        )

    if rate:
        try:
            rating = Rating(int(rate))
        except ValueError:
            return RedirectResponse(f"/deck/{deck_id}/study", status_code=303)

        outcome = await manager.run(answer_card, card_id_int, rating, deck_id=deck_id)
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

    await account.manager.run(undo_last_op)
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

    try:
        cid = int(card_id)
    except ValueError:
        card_id_truncated = card_id[:100]
        logger.warning(f"delete_note_get: can't parse card_id: '{card_id_truncated}'")
        err_msg = f"Card id '{card_id_truncated}' is not a valid number"
        return RedirectResponse(f"/deck/{deck_id}/study?error_msg={quote(err_msg, safe='')}", status_code=303)

    view = await account.manager.run(get_card_view, cid, "new", None)
    if view is None:
        errMsg = f"Card '{cid}' does not exist"
        return RedirectResponse(f"/deck/{deck_id}/study?error_msg={quote(errMsg, safe='')}", status_code=303)

    trueDeck = await account.manager.run(card_deck_matches_or_descends, view.deck_id, deck_id)
    if (not trueDeck):
        err = f"delete_note_get: invalid deck id '{deck_id}', card's deck id: '{view.deck_id}'"
        logger.warning(err)
        errMsg = f"Card '{cid}' does not belong to the current deck '{deck_id}'"
        return RedirectResponse(f"/deck/{deck_id}/study?error_msg={quote(errMsg, safe='')}", status_code=303)

    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "delete_card_confirm.html",
        {
            "version": __version__,
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

    try:
        cid = int(card_id)
        await account.manager.run(delete_note_by_card, deck_id, cid)
        logger.info("delete_note_post: card_id=%s deck_id=%s deleted", cid, deck_id)
        return RedirectResponse(f"/deck/{deck_id}/study", status_code=303)
    except ValueError as exc:
        excStr = str(exc)
        logger.warning(
            "delete_note_post: card_id=%s deck_id=%s failed: %s",
            card_id[:100],
            deck_id,
            excStr,
        )
        return RedirectResponse(
            f"/deck/{deck_id}/study?error_msg={quote(excStr, safe='')}",
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

    card_id = payload.get("card_id", "")
    flag = payload.get("flag", "")

    if not type(card_id) is int:
        logger.warning("set-card-flag: invalid card_id=%s", str(card_id)[:100])
        return Response(status_code=400, content = "invalid card_id")
    if not type(flag) is int:
        logger.warning("set-card-flag: invalid flag=%s", str(flag)[:100])
        return Response(status_code=400, content = "invalid flag")

    try:
        await account.manager.run(set_card_flag, card_id, flag)
        return Response(status_code=204)
    except ValueError as exc:
        excStr = str(exc)
        logger.warning("set-card-flag: %s", excStr[:100])
        return Response(status_code=400, content = excStr)

        
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

    if not type(card_id) is int:
        logger.warning("set-card-mark: invalid card_id=%s", str(card_id)[:100])
        return Response(status_code=400, content = "invalid card_id")
    if not type(marked) is bool:
        logger.warning("set-card-mark: invalid marked=%s", str(marked)[:100])
        return Response(status_code=400, content = "invalid marked")
    
    try:
        await account.manager.run(set_card_marked, card_id, marked)
        return Response(status_code=204)
    except ValueError as exc:
        excStr= str(exc)
        logger.warning("set-card-mark: %s", excStr[:100])
        return Response(status_code=400, content = excStr)
