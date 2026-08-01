"""Card review routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import __version__
from app.domain.scheduler import (
    CardIntervals,
    Rating,
    answer_card,
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
    """Renders the session-complete page with an auto-sync."""

    synced, sync_err, attempted = await _auto_sync_if_possible(account)
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
            "synced": synced,
            "sync_error": sync_err,
            "sync_attempted": attempted,
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


@router.post("/deck/{deck_id}/flag", response_model=None)
async def flag_post(
    deck_id: int,
    card_id: str = Form(""),
    flag: str = Form(""),
    account: Account | None = Depends(get_current_account_optional),
    _: None = Depends(require_csrf),
) -> RedirectResponse:
    """Sets the user flag (0..4) on a card and returns to the study page."""

    if account is None:
        return RedirectResponse("/login", status_code=303)

    if card_id and flag:
        try:
            card_id_int = int(card_id)
            flag_int = int(flag)
        except ValueError:
            return RedirectResponse(f"/deck/{deck_id}/study", status_code=303)

        try:
            await account.manager.run(set_card_flag, card_id_int, flag_int)
        except ValueError:
            logger.warning(
                "flag_post: invalid flag card_id=%s flag=%s deck_id=%s",
                card_id,
                flag,
                deck_id,
            )

    return RedirectResponse(f"/deck/{deck_id}/study", status_code=303)


@router.post("/deck/{deck_id}/mark", response_model=None)
async def mark_post(
    deck_id: int,
    card_id: str = Form(""),
    marked: str = Form(""),
    account: Account | None = Depends(get_current_account_optional),
    _: None = Depends(require_csrf),
) -> RedirectResponse:
    """Toggles the "marked" (star) state on a card and returns to the study page.

    The hidden ``marked`` field carries the desired state (``"0"`` / ``"1"``)
    so the template can pre-compute the toggle target without any JS.
    """

    if account is None:
        return RedirectResponse("/login", status_code=303)

    if card_id and marked:
        try:
            card_id_int = int(card_id)
            marked_bool = marked == "1"
        except ValueError:
            return RedirectResponse(f"/deck/{deck_id}/study", status_code=303)

        try:
            await account.manager.run(set_card_marked, card_id_int, marked_bool)
        except ValueError:
            logger.warning(
                "mark_post: invalid mark card_id=%s marked=%s deck_id=%s",
                card_id,
                marked,
                deck_id,
            )

    return RedirectResponse(f"/deck/{deck_id}/study", status_code=303)
