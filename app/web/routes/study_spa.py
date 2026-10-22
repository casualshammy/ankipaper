"""
JSON-SPA study routes.

The SPA client is a small vanilla-JS application served by
``GET /deck/{deck_id}/spa``; it talks back to the JSON endpoints
listed below. No legacy template is touched by this module.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.domain.scheduler import (
  AnswerOutcome,
  Rating,
  answer_card,
  deck_is_filtered,
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
from app.sync.client import is_sync_required_or_throw
from app.toolkit.account_toolkit import get_account_logger
from app.web.csrf import require_csrf
from app.web.deps import get_current_account_optional
from app.web.req_types import StudyFlagPayload, StudyMarkPayload, StudyStatePayload

_common_logger = logging.getLogger(__name__)
router = APIRouter()


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
    {
      "deck_id": deck_id, 
      "is_filtered": is_filtered
    },
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
  return JSONResponse(payload.model_dump())


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
    payload = await _build_front_payload(account, deck_id, "", logger)

  return JSONResponse(payload.model_dump())


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
  outcome: AnswerOutcome = await account.run_extended(answer_card, card_id, rate, deck_id=deck_id)
  if outcome.stale:
    logger.info(
        "study_spa_answer: stale answer card_id=%s deck_id=%s rate=%s",
        card_id,
        deck_id,
        int(rate),
    )

  payload = await _build_front_payload(account, deck_id, "", logger)
  return JSONResponse(payload.model_dump())


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
  return JSONResponse(payload.model_dump())


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

  undo = await account.run(get_undo_status)
  return JSONResponse(StudyMarkPayload.build(is_marked=marked, undo=undo).model_dump())


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

  undo = await account.run(get_undo_status)
  return JSONResponse(StudyFlagPayload.build(flag=flag, undo=undo).model_dump())


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
  return JSONResponse(payload.model_dump())


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


async def _is_filtered(account: Account, deck_id: int) -> bool:
  return bool(await account.run(deck_is_filtered, deck_id))


async def _is_sync_required(logger: logging.Logger, account: Account) -> bool:
  """True if AnkiWeb has changes we haven't pulled yet."""

  if account.sync_state.status == "running":
    return False
  
  try:
    result, _ = await is_sync_required_or_throw(account, account.sync_state.conflict_new_endpoint)
    return result
  except Exception as ex:
    logger.error("_is_sync_required failed: %s", ex)
    return True


async def _build_front_payload(
  account: Account,
  deck_id: int,
  error_msg: str,
  logger: logging.Logger,
) -> StudyStatePayload:
  """Front-phase payload: next due card or done."""

  view = await account.run_extended(get_next_card, deck_id)
  if view is None:
    return await _build_done_payload(account, deck_id, logger)
  
  breakdown = await account.run(get_deck_due_breakdown, deck_id)
  return StudyStatePayload.build(
    phase = "front",
    deck_id = deck_id,
    is_filtered = await _is_filtered(account, deck_id),
    has_cards = bool(await account.run(get_deck_card_count, deck_id)),
    undo = await account.run(get_undo_status),
    error_msg = error_msg,
    card = view,
    breakdown = breakdown,
    sync_required = await _is_sync_required(logger, account),
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
  return StudyStatePayload.build(
    phase = "back",
    deck_id = deck_id,
    is_filtered = await _is_filtered(account, deck_id),
    has_cards = bool(await account.run(get_deck_card_count, deck_id)),
    undo = await account.run(get_undo_status),
    error_msg = error_msg,
    card = view,
    breakdown = breakdown,
    sync_required = await _is_sync_required(logger, account),
  )


async def _build_done_payload(
  account: Account,
  deck_id: int,
  logger: logging.Logger,
) -> StudyStatePayload:
  """Done-phase payload."""
  
  return StudyStatePayload.for_done(
    deck_id = deck_id,
    is_filtered = await _is_filtered(account, deck_id),
    has_cards = bool(await account.run(get_deck_card_count, deck_id)),
    undo = await account.run(get_undo_status),
    sync_required = await _is_sync_required(logger, account),
  )


def _unauth() -> JSONResponse:
  return JSONResponse({"redirect": "/login"}, status_code=401)


def _bad_request(error_msg: str) -> JSONResponse:
  return JSONResponse({"error_msg": error_msg}, status_code=400)