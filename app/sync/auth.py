"""Авторизация в AnkiWeb через sync v3-протокол."""

from __future__ import annotations

import os
import tempfile

import anki.collection
from anki.errors import BackendError
from anki.sync_pb2 import SyncAuth

from app.config import get_settings

DEFAULT_ENDPOINT = "https://sync.ankiweb.net/"


class AuthError(RuntimeError):
    """Ошибка авторизации в AnkiWeb (неверные креды, сеть и т.п.)."""

def _open_temp_collection() -> tuple[anki.collection.Collection, str]:
    """Открывает временную коллекцию для sync_login.

    anki 26.x не поддерживает ``:memory:`` напрямую (os.path.abspath ломает
    псевдоним), поэтому создаём реальный временный файл.

    Returns:
        кортеж (открытая коллекция, путь к временному файлу).
    """

    fd, path = tempfile.mkstemp(prefix="kindlanki-login-", suffix=".anki21")
    os.close(fd)
    return anki.collection.Collection(path), path


def login(username: str, password: str) -> str:
    """Авторизуется в AnkiWeb и возвращает hostKey.

    Args:
        username: имя пользователя AnkiWeb.
        password: пароль AnkiWeb.

    Raises:
        AuthError: при ошибке авторизации или сети.
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
    """Собирает SyncAuth из ранее полученного hostKey.

    Args:
        host_key: hostKey, полученный при login.
        endpoint: URL конкретного sync-сервера (``sync20.ankiweb.net`` и т.п.).
            Если ``None`` — используется ``_endpoint()`` из настроек.
    """

    return SyncAuth(hkey=host_key, endpoint=endpoint or DEFAULT_ENDPOINT)


def _translate_backend_error(exc: BackendError) -> str:
    """Преобразует BackendError в человекочитаемое сообщение."""

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