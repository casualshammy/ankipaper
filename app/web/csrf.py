"""CSRF protection for state-changing POST endpoints.

The token is a stateless HMAC over ``{"sid": <account_id>}`` signed by
``itsdangerous`` with the same ``session.secret`` as the cookie
serializer (different salt). It is embedded as a hidden field in every
form and verified server-side.

The token is only useful when paired with the HttpOnly session cookie,
so a stolen token alone cannot authorise a request. We deliberately
do not store tokens in Redis or any other external store (see
``AGENTS.md`` section 1 — zero external infra).

On unauthenticated requests the dependency is a no-op: the route
handler will redirect to ``/login`` anyway, so there is nothing to
defend. ``/login`` itself is not protected because the CSRF token
would be bound to an empty ``sid`` and therefore constant across
visitors — providing no protection against login CSRF while breaking
the form for users without JS.
"""

from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request, status
from itsdangerous import BadSignature, URLSafeSerializer

from app.web.deps import get_session
from app.web.session import Session, make_serializer, read_session

logger = logging.getLogger(__name__)


async def require_csrf(
    request: Request,
    session: Session = Depends(get_session),
) -> None:
    """
    FastAPI dependency that validates the CSRF token on a request.

    Reads ``csrf_token`` from the form/header and verifies it against the current session's account id. 
    Raises 403 on any failure.

    Skipped when the request is not authenticated - the route handler will redirect to ``/login`` on its own, 
    so there is no privileged state to defend.
    """

    if not session.is_authenticated or not session.account_id:
        return

    token = request.headers.get("X-CSRF-Token", "")
    if not token:
        try:
            form = await request.form()
            token = str(form.get("csrf_token", ""))
        except Exception:
            pass

    if not _is_csrf_valid(token, session.account_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing, invalid or expired")


def csrf_token(request: Request) -> str:
    """
    Jinja2 global that returns the CSRF token for the current session.

    Registered via ``templates.env.globals`` so every template can
    render ``<input type="hidden" name="csrf_token" value="{{ csrf_token(request) }}">``
    without each route having to thread the value through its context.
    """

    session = read_session(request)
    if not session.is_authenticated or not session.account_id:
        return ""

    serializer = _csrf_serializer()
    if serializer is None:
        return ""

    token = serializer.dumps({
        "sid": session.account_id
    })
    return token


def _csrf_serializer() -> URLSafeSerializer | None:
    """
    Returns an itsdangerous serializer for CSRF tokens, or None.

    Reuses :func:`app.web.session.make_serializer` with a dedicated
    salt so cookie tokens and CSRF tokens are not interchangeable.
    """

    return make_serializer("ankipaper-csrf")

def _is_csrf_valid(token: str, account_id: str) -> bool:
    """
    Validates a CSRF token against the given account ID.

    Returns True if the token is valid, False otherwise.
    """

    if not token:
        return False
    
    serializer = _csrf_serializer()
    if serializer is None:
        return False
    
    try:
        data = serializer.loads(token)
    except BadSignature:
        return False
    
    if not isinstance(data, dict):
        return False

    token_account_id = data.get("sid")
    return token_account_id == account_id