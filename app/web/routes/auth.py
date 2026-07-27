"""Authentication routes: /login, /logout."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import __version__
from app.storage.account import get_account_store
from app.sync.auth import AuthError, login
from app.web.deps import get_session
from app.web.ratelimit import client_ip, get_login_rate_limiter
from app.web.session import Session, clear_session, write_session
from app.web.csrf import require_csrf

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/login", response_model=None)
async def login_get(
    request: Request,
    reason: str | None = None,
    session: Session = Depends(get_session),
) -> HTMLResponse | RedirectResponse:
    """Renders the login form, or redirects to the home page if already signed in."""

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
) -> HTMLResponse | RedirectResponse:
    """Accepts login/password, performs authentication, sets the cookie."""

    templates: Jinja2Templates = request.app.state.templates
    ip = client_ip(request)
    limiter = get_login_rate_limiter()

    # Rate-limit check before forwarding credentials to AnkiWeb. We
    # always charge the per-IP counter; the per-username counter only
    # applies once a non-empty username is supplied. Redis is verified
    # on every request — if it is unreachable we refuse the login
    # rather than silently disabling brute-force protection.
    try:
        error = await limiter.check(ip, username)
    except RuntimeError as exc:
        logger.error("Login rate limiter unavailable: %s", exc)
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "version": __version__,
                "account": None,
                "reason": None,
                "error": "Service temporarily unavailable. Please try again later.",
                "username": username,
            },
            status_code=503,
        )
    if error is not None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "version": __version__,
                "account": None,
                "reason": None,
                "error": error,
                "username": username,
            },
            status_code=429,
        )

    store = get_account_store()
    if not store.can_create_account(username, request.app.state.settings.data_max_bytes):
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "version": __version__,
                "account": None,
                "reason": None,
                "error": "New account registration is temporarily unavailable because the data storage limit has been reached.",
                "username": username,
            },
            status_code=401,
        )

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

    # Successful login — clear the counters so the user does not lock
    # themselves out by signing in and out repeatedly. Best-effort.
    await limiter.reset(ip, username)

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
async def logout_post(_: None = Depends(require_csrf)) -> RedirectResponse:
    """Deletes the cookie and redirects to /login.

    The hostKey and collection files are not removed — the user can sign
    back in to the same account. Full account deletion is a separate
    scenario (TODO: add ``/account/delete`` if needed).
    """

    response = RedirectResponse("/login", status_code=303)
    clear_session(response)
    return response
