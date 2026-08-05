"""Authentication with AnkiWeb via the sync v3 protocol."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from contextlib import contextmanager

import anki.collection
from anki.errors import BackendError
from anki.sync_pb2 import SyncAuth

from app.sync.endpoints import DEFAULT_ENDPOINT

# Lower-case substrings used to classify ``BackendError`` messages.
AUTH_ERROR_MARKERS: tuple[str, ...] = (
    "auth",
    "invalid",
    "credential",
    "incorrect",
    "email",
    "password",
    "token",
    "expired",
    "expire",
    "unauthorized",
)

NETWORK_ERROR_MARKERS: tuple[str, ...] = (
    "timeout",
    "connection",
    "dns",
    "unreachable",
    "refused",
)


class AuthError(RuntimeError):
    """AnkiWeb authentication error (invalid credentials, network issues, etc.)."""


@contextmanager
def _temp_collection() -> Generator[anki.collection.Collection, None, None]:
    """Yields a temporary ``Collection`` for ``sync_login``.

    anki 26.x does not support ``:memory:`` directly (``os.path.abspath``
    breaks the alias), so we create a real temporary file that is removed
    when the context exits.
    """

    fd, path = tempfile.mkstemp(prefix="ankipaper-login-", suffix=".anki21")
    os.close(fd)
    col = anki.collection.Collection(path)
    try:
        yield col
    finally:
        try:
            col.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            os.unlink(path)
        except OSError:
            pass


def login(username: str, password: str) -> str:
    """Logs into AnkiWeb and returns the hostKey.

    Args:
        username: AnkiWeb username.
        password: AnkiWeb password.

    Raises:
        AuthError: on authentication or network error.
    """

    if not username or not password:
        raise AuthError("Username and password are required")

    with _temp_collection() as col:
        try:
            auth: SyncAuth = col.sync_login(username, password, DEFAULT_ENDPOINT)
        except BackendError as exc:
            raise AuthError(_translate_backend_error(exc)) from exc
        except Exception as exc:
            raise AuthError(f"Network error: {exc}") from exc

    if not auth.hkey:
        raise AuthError("AnkiWeb returned an empty hostKey")

    return auth.hkey


def make_auth(host_key: str, endpoint: str | None = None) -> SyncAuth:
    """Builds a ``SyncAuth`` from a previously obtained hostKey.

    Args:
        host_key: hostKey obtained at login.
        endpoint: URL of the sync server (``sync20.ankiweb.net`` etc.).
            ``None`` falls back to :data:`app.sync.endpoints.DEFAULT_ENDPOINT`.
    """

    return SyncAuth(hkey=host_key, endpoint=endpoint or DEFAULT_ENDPOINT)


def is_auth_error(exc: BackendError) -> bool:
    """True if the ``BackendError`` indicates an expired/invalid hostKey.

    Used by :mod:`app.sync.client` to distinguish auth-failures (which
    should clear the stored hostKey and force re-login) from other sync
    errors that may be transient.
    """

    return _classify_backend_error(exc) == "auth"


def _classify_backend_error(exc: BackendError) -> str:
    """Returns ``"auth"``, ``"network"`` or ``""`` for the given ``BackendError``.

    A single source of truth for marker matching shared by
    :func:`is_auth_error` and :func:`_translate_backend_error`.
    """

    message = str(exc).lower()
    if any(marker in message for marker in AUTH_ERROR_MARKERS):
        return "auth"
    if any(marker in message for marker in NETWORK_ERROR_MARKERS):
        return "network"
    return ""


def _translate_backend_error(exc: BackendError) -> str:
    """Converts a ``BackendError`` into a human-readable message."""

    kind = _classify_backend_error(exc)
    if kind == "auth":
        return "Invalid AnkiWeb username or password"
    if kind == "network":
        return "Could not reach AnkiWeb, please try again"
    return f"AnkiWeb error: {exc}"