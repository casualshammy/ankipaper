"""Authentication with AnkiWeb via the sync v3 protocol."""

from __future__ import annotations

import os
import tempfile

import anki.collection
from anki.errors import BackendError
from anki.sync_pb2 import SyncAuth

from app.config import get_settings

DEFAULT_ENDPOINT = "https://sync.ankiweb.net/"


class AuthError(RuntimeError):
    """AnkiWeb authentication error (invalid credentials, network issues, etc.)."""

def _open_temp_collection() -> tuple[anki.collection.Collection, str]:
    """Opens a temporary collection for sync_login.

    anki 26.x does not support ``:memory:`` directly (``os.path.abspath``
    breaks the alias), so we create a real temporary file.

    Returns:
        tuple of (open collection, path to the temporary file).
    """

    fd, path = tempfile.mkstemp(prefix="ankipaper-login-", suffix=".anki21")
    os.close(fd)
    return anki.collection.Collection(path), path


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

    col, tmp_path = _open_temp_collection()
    try:
        auth: SyncAuth = col.sync_login(username, password, DEFAULT_ENDPOINT)
    except BackendError as exc:
        raise AuthError(_translate_backend_error(exc)) from exc
    except Exception as exc:
        raise AuthError(f"Network error: {exc}") from exc
    finally:
        try:
            col.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if not auth.hkey:
        raise AuthError("AnkiWeb returned an empty hostKey")

    return auth.hkey


def make_auth(host_key: str, endpoint: str | None = None) -> SyncAuth:
    """Builds a SyncAuth from a previously obtained hostKey.

    Args:
        host_key: hostKey obtained at login.
        endpoint: URL of a specific sync server (``sync20.ankiweb.net`` etc.).
            If ``None`` — uses ``_endpoint()`` from settings.
    """

    return SyncAuth(hkey=host_key, endpoint=endpoint or DEFAULT_ENDPOINT)


def _translate_backend_error(exc: BackendError) -> str:
    """Converts a BackendError into a human-readable message."""

    message = str(exc).lower()

    auth_markers = (
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
    if any(marker in message for marker in auth_markers):
        return "Invalid AnkiWeb username or password"

    network_markers = ("timeout", "connection", "dns", "unreachable", "refused")
    if any(marker in message for marker in network_markers):
        return "Could not reach AnkiWeb, please try again"

    return f"AnkiWeb error: {exc}"