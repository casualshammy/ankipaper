"""Cookie sessions: read and write via itsdangerous.

The ``session.secret`` file is created lazily on first access. Until
then ``_serializer()`` returns ``None``, and ``read_session()`` returns
"not authenticated". This is intentional: after login the file appears,
and subsequent requests read sessions correctly.

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

logger = logging.getLogger(__name__)

COOKIE_NAME = "kindlanki_session"
COOKIE_KEY = "user"


@dataclass(slots=True)
class Session:
    """Minimal session: authentication flag + current account."""

    is_authenticated: bool
    account_id: str | None = None


def _serializer() -> URLSafeSerializer | None:
    """Returns an itsdangerous serializer, or None if the key has not been created yet."""

    from app.storage.secrets import _fernet_key_path

    path = _fernet_key_path()
    if not path.exists():
        return None
    try:
        secret = path.read_bytes()
    except OSError as exc:
        logger.warning("Cannot read session secret: %s", exc)
        return None
    return URLSafeSerializer(secret, salt="kindlanki-cookie")


def _max_age_seconds() -> int:
    """Cookie lifetime in seconds."""

    return get_settings().cookie_max_age_days * 86400


def _secure_cookie() -> bool:
    """Returns True if the cookie should be set with the Secure flag."""

    return get_settings().behind_proxy


def read_session(request: Request) -> Session:
    """Reads the cookie and returns a Session. Does not fail if the secret is missing."""

    s = _serializer()
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

    Args:
        response: Response object on which to set the cookie.
        account_id: account identifier (``Account.id``).
    """

    s = _serializer()
    if s is None:
        from app.storage.secrets import _load_or_create_fernet_key

        s = URLSafeSerializer(_load_or_create_fernet_key(), salt="kindlanki-cookie")

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
