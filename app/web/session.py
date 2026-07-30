"""Cookie sessions: read and write via itsdangerous.

The ``session.secret`` file is created lazily on first access. Until
then ``make_serializer()`` returns ``None``, and ``read_session()``
returns "not authenticated". This is intentional: after login the
file appears, and subsequent requests read sessions correctly.

The same secret is reused (with a different salt) for CSRF token
signing in :mod:`app.web.csrf` — see :func:`make_serializer`.

In the multi-account version the cookie stores
``{"user": "<account_id>"}`` — the id by which :class:`AccountStore`
finds the right account in memory (or loads it from disk).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from fastapi import Request, Response
from itsdangerous import BadSignature, URLSafeSerializer

from app.config import get_settings
from app.storage.secrets import (
    _load_or_create_fernet_key,
    read_session_secret_bytes,
)

logger = logging.getLogger(__name__)

COOKIE_NAME = "ankipaper_session"
COOKIE_KEY = "user"
COOKIE_SALT = "ankipaper-cookie"

@dataclass(slots=True)
class Session:
    """Minimal session: authentication flag + current account."""

    is_authenticated: bool
    account_id: str | None = None


def make_serializer(salt: str) -> URLSafeSerializer | None:
    """Returns an itsdangerous serializer bound to the session secret, or None.

    The same secret is reused with different salts: ``COOKIE_SALT`` here
    for cookies, and ``"ankipaper-csrf"`` in :mod:`app.web.csrf` for
    CSRF tokens. Centralising the load-or-None logic keeps both callers
    in sync. Returns None before the first login — callers must treat
    that as "not authenticated".
    """

    secret = read_session_secret_bytes()
    if secret is None:
        return None
    return URLSafeSerializer(secret, salt=salt)


def _max_age_seconds() -> int:
    """Cookie lifetime in seconds."""

    return get_settings().cookie_max_age_days * 86400


def _secure_cookie() -> bool:
    """Returns True if the cookie should be set with the Secure flag."""

    return get_settings().behind_proxy


def read_session(request: Request) -> Session:
    """Reads the cookie and returns a Session. Does not fail if the secret is missing."""

    s = make_serializer(COOKIE_SALT)
    if s is None:
        return Session(is_authenticated=False)

    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return Session(is_authenticated=False)

    try:
        data: Any = s.loads(raw)
    except BadSignature:
        return Session(is_authenticated=False)

    if not isinstance(data, dict):
        return Session(is_authenticated=False)

    user = data.get(COOKIE_KEY)
    if not isinstance(user, str) or not user:
        return Session(is_authenticated=False)

    return Session(is_authenticated=True, account_id=user)


def write_session(response: Response, account_id: str) -> None:
    """Sets an authenticated session cookie for the given account.

    Uses :func:`app.storage.secrets._load_or_create_fernet_key` so the
    secret file is created on first login rather than failing with a
    missing-key error.

    Args:
        response: Response object on which to set the cookie.
        account_id: account identifier (``Account.id``).
    """

    s = URLSafeSerializer(_load_or_create_fernet_key(), salt=COOKIE_SALT)
    token = s.dumps({COOKIE_KEY: account_id})
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=_max_age_seconds(),
        httponly=True,
        samesite="lax",
        secure=_secure_cookie(),
        path="/",
    )


def clear_session(response: Response) -> None:
    """Deletes the session cookie."""

    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        secure=_secure_cookie(),
        httponly=True,
        samesite="lax",
    )
