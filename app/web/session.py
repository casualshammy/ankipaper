"""Cookie-сессии: чтение и запись через itsdangerous.

Файл ``session.secret`` создаётся лениво при первом обращении. До этого
момента ``_serializer()`` возвращает None, а ``read_session()`` —
«не аутентифицирован». Это намеренно: после login файл появляется, и
последующие запросы читают сессии корректно.

С версией с поддержкой нескольких аккаунтов cookie хранит
``{"user": "<account_id>"}`` — это id, по которому :class:`AccountStore`
находит нужный аккаунт в памяти (или подгружает с диска).
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
    """Минимальная сессия: факт аутентификации + текущий аккаунт."""

    is_authenticated: bool
    account_id: str | None = None


def _serializer() -> URLSafeSerializer | None:
    """Возвращает itsdangerous-сериализатор или None, если ключ ещё не создан."""

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
    """Время жизни cookie в секундах."""

    return get_settings().cookie_max_age_days * 86400


def _secure_cookie() -> bool:
    """Возвращает True, если cookie нужно ставить с флагом Secure."""

    return get_settings().behind_proxy


def read_session(request: Request) -> Session:
    """Читает cookie и возвращает Session. Без падения при отсутствии секрета."""

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
    """Ставит cookie аутентифицированной сессии для указанного аккаунта.

    Args:
        response: объект Response, в который ставится cookie.
        account_id: идентификатор аккаунта (``Account.id``).
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
    """Удаляет cookie сессии."""

    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        secure=_secure_cookie(),
        httponly=True,
        samesite="lax",
    )
