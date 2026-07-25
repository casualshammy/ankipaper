"""Роуты авторизации: /login, /logout."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import __version__
from app.storage import secrets
from app.sync.auth import AuthError, login
from app.web.deps import get_session
from app.web.session import Session, clear_session, write_session

logger = logging.getLogger(__name__)

router = APIRouter()

HOSTKEY_SECRET_NAME = "ankiweb_hostkey"


@router.get("/login", response_model=None)
async def login_get(
    request: Request,
    reason: str | None = None,
    session: Session = Depends(get_session),
) -> HTMLResponse | RedirectResponse:
    """Отображает форму логина или редиректит на главную, если уже залогинен."""

    if session.is_authenticated and secrets.load_secret(HOSTKEY_SECRET_NAME):
        return RedirectResponse("/", status_code=303)

    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "version": __version__,
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
                "reason": None,
                "error": str(exc),
                "username": username,
            },
            status_code=401,
        )

    secrets.save_secret(HOSTKEY_SECRET_NAME, host_key)
    response = RedirectResponse("/", status_code=303)
    write_session(response)
    return response


@router.post("/logout", response_model=None)
async def logout_post() -> RedirectResponse:
    """Удаляет hostKey и cookie, редиректит на /login."""

    secrets.delete_secret(HOSTKEY_SECRET_NAME)
    response = RedirectResponse("/login", status_code=303)
    clear_session(response)
    return response