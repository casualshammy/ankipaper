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
from app.web.session import Session

logger = logging.getLogger(__name__)


def _csrf_serializer() -> URLSafeSerializer | None:
    """Returns an itsdangerous serializer for CSRF tokens, or None.

    Mirrors :func:`app.web.session._serializer` but uses a dedicated
    salt so cookie tokens and CSRF tokens are not interchangeable.
    """

    from app.storage.secrets import _fernet_key_path

    path = _fernet_key_path()
    if not path.exists():
        return None
    try:
        secret = path.read_bytes()
    except OSError as exc:
        logger.warning("Cannot read session secret for CSRF: %s", exc)
        return None
    return URLSafeSerializer(secret, salt="ankipaper-csrf")


def make_csrf_token(sid: str) -> str:
    """Signs a CSRF token bound to ``sid`` (the current account id).

    Args:
        sid: account id from the session, or an empty string for
            anonymous requests.

    Returns:
        Signed token, or an empty string if the session secret has
        not been generated yet (the page still renders, but the
        corresponding endpoint will reject the empty token if CSRF
        protection is enforced there).
    """

    s = _csrf_serializer()
    if s is None:
        return ""
    return s.dumps({"sid": sid})


def verify_csrf_token(token: str, expected_sid: str) -> bool:
    """Returns ``True`` iff ``token`` is a valid CSRF token for ``expected_sid``."""

    if not token:
        return False
    s = _csrf_serializer()
    if s is None:
        return False
    try:
        data = s.loads(token)
    except BadSignature:
        return False
    if not isinstance(data, dict):
        return False
    return data.get("sid") == expected_sid


async def require_csrf(
    request: Request,
    session: Session = Depends(get_session),
) -> None:
    """FastAPI dependency that validates the CSRF token on a POST request.

    Reads ``csrf_token`` from the form and verifies it against the
    current session's account id. Raises 403 on any failure.

    Skipped when the request is not authenticated — the route handler
    will redirect to ``/login`` on its own, so there is no privileged
    state to defend.
    """

    if not session.is_authenticated:
        return

    try:
        form = await request.form()
    except Exception:  # noqa: BLE001 — multipart parse failures are treated as no token
        form = {}

    token = str(form.get("csrf_token", "")) if isinstance(form, dict) else str(form.get("csrf_token", ""))
    expected_sid = session.account_id or ""
    if not verify_csrf_token(token, expected_sid):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token invalid",
        )


def csrf_token(request: Request) -> str:
    """Jinja2 global that returns the CSRF token for the current session.

    Registered via ``templates.env.globals`` so every template can
    render ``<input type="hidden" name="csrf_token" value="{{ csrf_token(request) }}">``
    without each route having to thread the value through its context.
    """

    from app.web.session import read_session

    session = read_session(request)
    return make_csrf_token(session.account_id or "")
