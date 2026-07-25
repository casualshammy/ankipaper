"""Роуты ревью карточек."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import __version__
from app.domain.scheduler import (
    Rating,
    answer_card,
    get_card_view,
    get_deck_due_breakdown,
    get_next_card,
)
from app.storage import get_collection_manager, secrets
from app.sync.client import try_sync
from app.web.deps import get_session
from app.web.session import Session

logger = logging.getLogger(__name__)

router = APIRouter()

HOSTKEY_SECRET_NAME = "ankiweb_hostkey"


def _host_key() -> str | None:
    """Возвращает сохранённый hostKey или None."""

    return secrets.load_secret(HOSTKEY_SECRET_NAME)


async def _session_done(
    request: Request,
    manager,
    deck_id: int,
) -> HTMLResponse:
    """Отображает страницу завершения сессии с авто-sync."""

    synced, sync_err, attempted = await _auto_sync_if_possible(manager, _host_key())
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "study_done.html",
        {
            "version": __version__,
            "deck_id": deck_id,
            "remaining": 0,
            "remaining_new": 0,
            "remaining_learning": 0,
            "remaining_review": 0,
            "synced": synced,
            "sync_error": sync_err,
            "sync_attempted": attempted,
        },
    )


async def _auto_sync_if_possible(
    manager,
    host_key: str | None,
) -> tuple[bool, str | None, bool]:
    """Best-effort sync после завершения сессии ревью."""

    if not host_key:
        return False, None, False

    result = await manager.run(try_sync, host_key)
    return (result.required, result.error, True)


@router.get("/deck/{deck_id}/study", response_model=None)
async def study_get(
    request: Request,
    deck_id: int,
    session: Session = Depends(get_session),
) -> HTMLResponse | RedirectResponse:
    """Показывает front следующей due-карточки или страницу завершения."""

    if not session.is_authenticated:
        return RedirectResponse("/login", status_code=303)

    templates: Jinja2Templates = request.app.state.templates
    manager = get_collection_manager()

    view = await manager.run(get_next_card, deck_id)
    if view is None:
        return await _session_done(request, manager, deck_id)

    breakdown = await manager.run(get_deck_due_breakdown, deck_id)
    return templates.TemplateResponse(
        request,
        "study_front.html",
        {
            "version": __version__,
            "deck_id": deck_id,
            "card": view,
            "remaining": breakdown.total,
            "remaining_new": breakdown.new,
            "remaining_learning": breakdown.learning,
            "remaining_review": breakdown.review,
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
    session: Session = Depends(get_session),
) -> HTMLResponse | RedirectResponse:
    """Обрабатывает Reveal / Answer в рамках сессии ревью."""

    if not session.is_authenticated:
        return RedirectResponse("/login", status_code=303)

    templates: Jinja2Templates = request.app.state.templates
    manager = get_collection_manager()

    if not card_id:
        return RedirectResponse(f"/deck/{deck_id}/study", status_code=303)

    try:
        card_id_int = int(card_id)
    except ValueError:
        return RedirectResponse(f"/deck/{deck_id}/study", status_code=303)

    if reveal:
        # ``card_type`` приходит с фронта через скрытое поле формы,
        # чтобы подсветить тег типа на обратной стороне. Фоллбэк на
        # ``"new"`` — на случай, если форма отправлена без поля.
        view = await manager.run(get_card_view, card_id_int, card_type or "new")
        if view is None:
            return RedirectResponse(f"/deck/{deck_id}/study", status_code=303)

        breakdown = await manager.run(get_deck_due_breakdown, deck_id)
        return templates.TemplateResponse(
            request,
            "study_back.html",
            {
                "version": __version__,
                "deck_id": deck_id,
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
        if outcome.next_card_id is None:
            return await _session_done(request, manager, deck_id)
        return RedirectResponse(f"/deck/{deck_id}/study", status_code=303)

    return RedirectResponse(f"/deck/{deck_id}/study", status_code=303)