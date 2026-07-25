"""Роуты авторизации: /login, /logout."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import __version__
from app.storage.account import get_account_store
from app.sync.auth import AuthError, login
from app.web.deps import get_session
from app.web.session import Session, clear_session, write_session

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/login", response_model=None)
async def login_get(
    request: Request,
    reason: str | None = None,
    session: Session = Depends(get_session),
) -> HTMLResponse | RedirectResponse:
    """Отображает форму логина или редиректит на главную, если уже залогинен."""

    if session.is_authenticated and session.account_id:
        return RedirectResponse("/", status_code=303)

    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "version": __version__,
            "account": None,
            "reason": reason,
            "error": None,
            "username": "",
        },
    )


@router.post("/login", response_model=None)
async def login_post(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
) -> HTMLResponse:
    """Принимает логин/пароль, выполняет авторизацию, ставит cookie."""

    templates: Jinja2Templates = request.app.state.templates
    try:
        host_key = login(username, password)
    except AuthError as exc:
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "version": __version__,
                "account": None,
                "reason": None,
                "error": str(exc),
                "username": username,
            },
            status_code=401,
        )

    store = get_account_store()
    account = store.get_or_create(username)
    account.save_host_key(host_key)
    logger.info(
        "Login successful: account_id=%s username=%s",
        account.id,
        account.username,
    )

    response = RedirectResponse("/", status_code=303)
    write_session(response, account.id)
    return response


@router.post("/logout", response_model=None)
async def logout_post() -> RedirectResponse:
    """Удаляет cookie, редиректит на /login.

    hostKey и файлы коллекции не удаляются — пользователь может снова
    залогиниться в этот же аккаунт. Полное удаление аккаунта —
    отдельный сценарий (TODO: при необходимости добавить ``/account/delete``).
    """

    response = RedirectResponse("/login", status_code=303)
    clear_session(response)
    return response
