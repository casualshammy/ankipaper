"""JSON-SPA study routes.

Self-contained module: all new study endpoints live under
``/deck/{deck_id}/spa/...`` and coexist with the legacy
``/deck/{deck_id}/study`` HTML flow in ``app.web.routes.study``.

The SPA client is a small vanilla-JS application served by
``GET /deck/{deck_id}/spa``; it talks back to the JSON endpoints
listed below. No legacy template is touched by this module.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.domain.scheduler import (
    AnswerOutcome,
    CardView,
    Rating,
    UndoInfo,
    answer_card,
    delete_note_by_card,
    empty_filtered_deck,
    get_card_intervals,
    get_card_view,
    get_deck_card_count,
    get_deck_due_breakdown,
    get_next_card,
    get_undo_status,
    rebuild_filtered_deck,
    set_card_flag,
    set_card_marked,
    undo_last_op,
)
from app.storage.account import Account
from app.toolkit.account_toolkit import get_account_logger
from app.web.csrf import require_csrf
from app.web.deps import get_current_account_optional

_common_logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------

_CardTypeLabel = Literal["new", "learning", "review"]


@dataclass(slots=True)
class StudyStatePayload:
    """Full SPA state for one review phase."""

    phase: Literal["front", "back", "done"]
    deck_id: int
    is_filtered: bool
    has_cards: bool
    undo: UndoInfo
    error_msg: str
    card: CardView | None
    remaining_total: int
    remaining_new: int
    remaining_learn: int
    remaining_review: int
    card_type: _CardTypeLabel

    def to_json(self) -> dict[str, Any]:
        card_json = _card_view_to_json(self.card) if self.card is not None else None
        return {
            "phase": self.phase,
            "deck_id": self.deck_id,
            "is_filtered": self.is_filtered,
            "has_cards": self.has_cards,
            "undo": {
                "label": self.undo.label,
                "can_undo": self.undo.can_undo,
                "can_redo": self.undo.can_redo,
            },
            "error_msg": self.error_msg,
            "card": card_json,
            "remaining_total": self.remaining_total,
            "remaining_new": self.remaining_new,
            "remaining_learn": self.remaining_learn,
            "remaining_review": self.remaining_review,
            "card_type": self.card_type,
        }


def _card_view_to_json(card: CardView) -> dict[str, Any]:
    intervals: dict[str, str] | None = None
    if card.intervals is not None:
        intervals = {
            "again": card.intervals.again,
            "hard": card.intervals.hard,
            "good": card.intervals.good,
            "easy": card.intervals.easy,
        }
    return {
        "card_id": card.card_id,
        "deck_id": card.deck_id,
        "question_html": card.question_html,
        "answer_html": card.answer_html,
        "is_cloze": card.is_cloze,
        "note_type_name": card.note_type_name,
        "fields": [list(f) for f in card.fields],
        "card_type": card.css_class.removeprefix("tag-current-"),
        "intervals": intervals,
        "flag": card.flag,
        "is_marked": card.is_marked,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _card_type_label(card: CardView) -> _CardTypeLabel:
    """Short label for the current card type used for chip highlighting."""

    cls = card.css_class
    if cls == "tag-current-learning":
        return "learning"
    if cls == "tag-current-review":
        return "review"
    return "new"


async def _is_filtered(account: Account, deck_id: int) -> bool:
    return bool(await account.run(_deck_is_filtered, deck_id))


def _deck_is_filtered(col, deck_id: int) -> bool:
    """True if the deck is a filtered (cram) deck."""

    return bool(col.decks.is_filtered(int(deck_id)))


async def _build_front_payload(
    account: Account,
    deck_id: int,
    error_msg: str,
    logger: logging.Logger,
) -> StudyStatePayload:
    """Front-phase payload: next due card or done."""

    view = await account.run_extended(get_next_card, deck_id)
    if view is None:
        return await _build_done_payload(account, deck_id, error_msg, logger)
    breakdown = await account.run(get_deck_due_breakdown, deck_id)
    return StudyStatePayload(
        phase="front",
        deck_id=deck_id,
        is_filtered=await _is_filtered(account, deck_id),
        has_cards=bool(await account.run(get_deck_card_count, deck_id)),
        undo=await account.run(get_undo_status),
        error_msg=error_msg,
        card=view,
        remaining_total=breakdown.total,
        remaining_new=breakdown.new,
        remaining_learn=breakdown.learning,
        remaining_review=breakdown.review,
        card_type=_card_type_label(view),
    )


async def _build_back_payload(
    account: Account,
    deck_id: int,
    card_id: int,
    error_msg: str,
    logger: logging.Logger,
) -> StudyStatePayload | None:
    """Back-phase payload; ``None`` if the card no longer exists."""

    intervals = await account.run(get_card_intervals, card_id)
    view = await account.run(get_card_view, card_id, intervals)
    if view is None:
        return None
    breakdown = await account.run(get_deck_due_breakdown, deck_id)
    return StudyStatePayload(
        phase="back",
        deck_id=deck_id,
        is_filtered=await _is_filtered(account, deck_id),
        has_cards=bool(await account.run(get_deck_card_count, deck_id)),
        undo=await account.run(get_undo_status),
        error_msg=error_msg,
        card=view,
        remaining_total=breakdown.total,
        remaining_new=breakdown.new,
        remaining_learn=breakdown.learning,
        remaining_review=breakdown.review,
        card_type=_card_type_label(view),
    )


async def _build_done_payload(
    account: Account,
    deck_id: int,
    error_msg: str,
    logger: logging.Logger,
) -> StudyStatePayload:
    return StudyStatePayload(
        phase="done",
        deck_id=deck_id,
        is_filtered=await _is_filtered(account, deck_id),
        has_cards=bool(await account.run(get_deck_card_count, deck_id)),
        undo=await account.run(get_undo_status),
        error_msg=error_msg,
        card=None,
        remaining_total=0,
        remaining_new=0,
        remaining_learn=0,
        remaining_review=0,
        card_type="new",
    )


def _unauth() -> JSONResponse:
    return JSONResponse({"redirect": "/login"}, status_code=401)


def _bad_request(error_msg: str) -> JSONResponse:
    return JSONResponse({"error_msg": error_msg}, status_code=400)


# ---------------------------------------------------------------------------
# HTML page
# ---------------------------------------------------------------------------

@router.get("/deck/{deck_id}/spa", response_model=None)
async def study_spa_get(
    request: Request,
    deck_id: int,
    account: Account | None = Depends(get_current_account_optional),
) -> HTMLResponse | JSONResponse:
    """Render the SPA shell. JS does the rest via /spa/state."""

    if account is None:
        return JSONResponse({"redirect": "/login"}, status_code=401)
    is_filtered = await _is_filtered(account, deck_id)
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "study_spa.html",
        {"deck_id": deck_id, "is_filtered": is_filtered},
    )


# ---------------------------------------------------------------------------
# JSON endpoints
# ---------------------------------------------------------------------------

@router.get("/deck/{deck_id}/spa/state", response_model=None)
async def study_spa_state_get(
    deck_id: int,
    account: Account | None = Depends(get_current_account_optional),
) -> JSONResponse:
    """Returns the next front-phase payload (or done) for the deck."""

    if account is None:
        return _unauth()
    logger = get_account_logger(_common_logger, account.id)
    payload = await _build_front_payload(account, deck_id, "", logger)
    return JSONResponse(payload.to_json())


@router.post("/deck/{deck_id}/spa/reveal", response_model=None)
async def study_spa_reveal_post(
    deck_id: int,
    card_id: int = Form(gt=0),
    account: Account | None = Depends(get_current_account_optional),
    _: None = Depends(require_csrf),
) -> JSONResponse:
    if account is None:
        return _unauth()
    logger = get_account_logger(_common_logger, account.id)
    payload = await _build_back_payload(account, deck_id, card_id, "", logger)
    if payload is None:
        # Card vanished; fall back to the head of the queue.
        payload = await _build_front_payload(account, deck_id, "", logger)
    return JSONResponse(payload.to_json())


@router.post("/deck/{deck_id}/spa/answer", response_model=None)
async def study_spa_answer_post(
    deck_id: int,
    card_id: int = Form(gt=0),
    rate: Rating = Form(gt=0),
    account: Account | None = Depends(get_current_account_optional),
    _: None = Depends(require_csrf),
) -> JSONResponse:
    if account is None:
        return _unauth()
    logger = get_account_logger(_common_logger, account.id)
    outcome: AnswerOutcome = await account.run_extended(
        answer_card, card_id, rate, deck_id=deck_id
    )
    if outcome.stale:
        logger.info(
            "study_spa_answer: stale answer card_id=%s deck_id=%s rate=%s",
            card_id,
            deck_id,
            int(rate),
        )
    # In both stale and fresh cases, re-derive the next phase from the queue.
    payload = await _build_front_payload(account, deck_id, "", logger)
    return JSONResponse(payload.to_json())


@router.post("/deck/{deck_id}/spa/undo", response_model=None)
async def study_spa_undo_post(
    deck_id: int,
    account: Account | None = Depends(get_current_account_optional),
    _: None = Depends(require_csrf),
) -> JSONResponse:
    if account is None:
        return _unauth()
    logger = get_account_logger(_common_logger, account.id)
    await account.run(undo_last_op)
    payload = await _build_front_payload(account, deck_id, "", logger)
    return JSONResponse(payload.to_json())


@router.post("/deck/{deck_id}/spa/mark", response_model=None)
async def study_spa_mark_post(
    deck_id: int,
    card_id: int = Form(gt=0),
    marked: bool = Form(),
    account: Account | None = Depends(get_current_account_optional),
    _: None = Depends(require_csrf),
) -> JSONResponse:
    if account is None:
        return _unauth()
    logger = get_account_logger(_common_logger, account.id)
    try:
        await account.run(set_card_marked, card_id, marked)
    except ValueError as exc:
        logger.warning("study_spa_mark: %s", exc)
        return _bad_request(str(exc))
    return JSONResponse({"is_marked": marked})


@router.post("/deck/{deck_id}/spa/flag", response_model=None)
async def study_spa_flag_post(
    deck_id: int,
    card_id: int = Form(gt=0),
    flag: int = Form(),
    account: Account | None = Depends(get_current_account_optional),
    _: None = Depends(require_csrf),
) -> JSONResponse:
    if account is None:
        return _unauth()
    logger = get_account_logger(_common_logger, account.id)
    try:
        await account.run(set_card_flag, card_id, flag)
    except ValueError as exc:
        logger.warning("study_spa_flag: %s", exc)
        return _bad_request(str(exc))
    return JSONResponse({"flag": flag})


@router.post("/deck/{deck_id}/spa/delete", response_model=None)
async def study_spa_delete_post(
    deck_id: int,
    card_id: int = Form(gt=0),
    account: Account | None = Depends(get_current_account_optional),
    _: None = Depends(require_csrf),
) -> JSONResponse:
    if account is None:
        return _unauth()
    logger = get_account_logger(_common_logger, account.id)
    try:
        await account.run(delete_note_by_card, deck_id, card_id)
    except ValueError as exc:
        logger.warning("study_spa_delete: %s", exc)
        return _bad_request(str(exc))
    logger.info("study_spa_delete: card_id=%s deck_id=%s deleted", card_id, deck_id)
    payload = await _build_front_payload(account, deck_id, "", logger)
    return JSONResponse(payload.to_json())


@router.post("/deck/{deck_id}/spa/rebuild", response_model=None)
async def study_spa_rebuild_post(
    deck_id: int,
    account: Account | None = Depends(get_current_account_optional),
    _: None = Depends(require_csrf),
) -> JSONResponse:
    if account is None:
        return _unauth()
    logger = get_account_logger(_common_logger, account.id)
    try:
        count = await account.run(rebuild_filtered_deck, deck_id)
    except Exception as exc:
        logger.warning("study_spa_rebuild failed: %s", exc)
        return _bad_request(str(exc))
    logger.info("study_spa_rebuild: deck_id=%s count=%s", deck_id, count)
    return JSONResponse({"ok": True, "count": count})


@router.post("/deck/{deck_id}/spa/empty", response_model=None)
async def study_spa_empty_post(
    deck_id: int,
    account: Account | None = Depends(get_current_account_optional),
    _: None = Depends(require_csrf),
) -> JSONResponse:
    if account is None:
        return _unauth()
    logger = get_account_logger(_common_logger, account.id)
    try:
        count = await account.run(empty_filtered_deck, deck_id)
    except Exception as exc:
        logger.warning("study_spa_empty failed: %s", exc)
        return _bad_request(str(exc))
    logger.info("study_spa_empty: deck_id=%s count=%s", deck_id, count)
    return JSONResponse({"ok": True, "count": count})
