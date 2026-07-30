"""Storing secrets on disk via Fernet.

Two instance-wide keys live under ``/data``:

- ``session.secret`` — signs session cookies and CSRF tokens.
- ``hostkey.secret`` — encrypts per-account ``hostkey.enc``.

The keys are kept separate so a leak of one does not expose the other.
Both files are created lazily on first access with mode 0600.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_PERMISSIONS = 0o600


def _fernet_key_path() -> Path:
    """Returns the path to the Fernet key file."""

    return Path("/data/session.secret")


def _ensure_data_dir() -> None:
    """Ensures that the parent directory for secrets exists."""

    path = _fernet_key_path()
    path.parent.mkdir(parents=True, exist_ok=True)


def _load_or_create_fernet_key() -> bytes:
    """Loads the Fernet key from the file or creates a new one.

    The file is created with mode 0600.
    """

    path = _fernet_key_path()
    _ensure_data_dir()

    if path.exists():
        return path.read_bytes().strip()

    key = Fernet.generate_key()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, DEFAULT_PERMISSIONS)
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    logger.info("Generated new session secret at %s", path)
    return key


def read_session_secret_bytes() -> bytes | None:
    """Returns the session secret bytes (stripped), or None.

    The session secret is the same key reused for two purposes: signing
    session cookies (``app.web.session``) and signing CSRF tokens
    (``app.web.csrf``). Sharing this helper keeps the "no secret yet"
    and I/O-error handling in one place. Returns None before the first
    login (no file on disk) or if the file cannot be read — callers
    must treat None as "not authenticated".

    ``.strip()`` is applied because the key is later used as HMAC
    material and a stray trailing newline would change the digest.
    """

    path = _fernet_key_path()
    if not path.exists():
        return None
    try:
        return path.read_bytes().strip()
    except OSError as exc:
        logger.warning("Cannot read session secret: %s", exc)
        return None


def _fernet() -> Fernet | None:
    """Returns a Fernet instance, or None if the key has not been created yet.

    Before the first login the file may be missing — this is fine, do not fail.
    """

    path = _fernet_key_path()
    if not path.exists():
        return None
    try:
        return Fernet(path.read_bytes().strip())
    except (ValueError, OSError) as exc:
        logger.warning("Failed to load session secret: %s", exc)
        return None


_HOSTKEY_KEY_FILE = "hostkey.secret"


def _hostkey_fernet_key_path() -> Path:
    """Returns the path to the hostKey encryption Fernet key file."""

    return Path("/data") / _HOSTKEY_KEY_FILE


def _load_or_create_hostkey_fernet_key() -> bytes:
    """Loads the hostKey Fernet key from the file or creates a new one.

    The file is created with mode 0600.
    """

    path = _hostkey_fernet_key_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        return path.read_bytes().strip()

    key = Fernet.generate_key()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, DEFAULT_PERMISSIONS)
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    logger.info("Generated new hostkey secret at %s", path)
    return key


def _hostkey_fernet() -> Fernet | None:
    """Returns a Fernet instance for hostKey encryption, or None if the
    key file has not been created yet."""

    path = _hostkey_fernet_key_path()
    if not path.exists():
        return None
    try:
        return Fernet(path.read_bytes().strip())
    except (ValueError, OSError) as exc:
        logger.warning("Failed to load hostkey secret: %s", exc)
        return None


def _save_secret_at(path: Path, value: str, *, name_for_log: str) -> None:
    """Encrypts ``value`` and saves it to ``path`` (mode 0600).

    Args:
        path: full path to the secret file.
        value: plaintext value to encrypt.
        name_for_log: human-readable name for logs (e.g. ``hostkey.enc``).
    """

    f = _fernet() or Fernet(_load_or_create_fernet_key())
    encrypted = f.encrypt(value.encode("utf-8"))

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, DEFAULT_PERMISSIONS)
    try:
        os.write(fd, encrypted)
    finally:
        os.close(fd)
    logger.info("Saved secret %s", name_for_log)


def _load_secret_at(path: Path, *, name_for_log: str) -> str | None:
    """Decrypts and returns the secret at the given path, or None."""

    if not path.exists():
        return None

    f = _fernet()
    if f is None:
        logger.warning("Cannot decrypt %s: session secret not available", name_for_log)
        return None

    try:
        return f.decrypt(path.read_bytes()).decode("utf-8")
    except (InvalidToken, OSError) as exc:
        logger.warning("Failed to decrypt %s: %s", name_for_log, exc)
        return None


def _delete_secret_at(path: Path) -> None:
    """Deletes the secret file at the given path. Errors are ignored."""

    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Failed to delete %s: %s", path, exc)


def save_secret(name: str, value: str) -> None:
    """Encrypts ``value`` and saves it to ``<data_dir>/<name>`` (mode 0600).

    Used for instance-wide global secrets (currently not used; kept for
    backward compatibility and future global secrets).

    Args:
        name: file name (e.g. ``"hostkey.enc"``).
        value: plaintext value to encrypt.
    """

    path = Path("/data") / name
    _save_secret_at(path, value, name_for_log=name)


def load_secret(name: str) -> str | None:
    """Decrypts and returns the secret from ``<data_dir>/<name>``, or None.

    Args:
        name: secret file name (e.g. ``"hostkey.enc"``).
    """

    return _load_secret_at(Path("/data") / name, name_for_log=name)


def delete_secret(name: str) -> None:
    """Deletes the secret file if it exists. Errors are ignored."""

    _delete_secret_at(Path("/data") / name)


def save_secret_in(account_dir: Path, name: str, value: str) -> None:
    """Encrypts ``value`` with the hostKey Fernet and saves it to
    ``<account_dir>/<name>`` (mode 0600).

    Uses a dedicated ``hostkey.secret`` key, separate from ``session.secret``,
    so a leak of one does not reveal the other.

    Args:
        account_dir: account directory (``data/accounts/<id>``).
        name: file name (e.g. ``"hostkey.enc"``).
        value: plaintext value to encrypt.
    """

    path = account_dir / name
    f = _hostkey_fernet() or Fernet(_load_or_create_hostkey_fernet_key())
    encrypted = f.encrypt(value.encode("utf-8"))

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, DEFAULT_PERMISSIONS)
    try:
        os.write(fd, encrypted)
    finally:
        os.close(fd)
    logger.info("Saved secret %s", f"{account_dir.name}/{name}")


def load_secret_in(account_dir: Path, name: str) -> str | None:
    """Decrypts ``<account_dir>/<name>`` using the hostKey Fernet, or ``None``.

    Returns ``None`` if the ciphertext file does not exist or cannot be
    decrypted with the current ``hostkey.secret``.

    Args:
        account_dir: account directory (``data/accounts/<id>``).
        name: secret file name.
    """

    path = account_dir / name
    if not path.exists():
        return None

    f = _hostkey_fernet()
    if f is None:
        logger.warning(
            "Cannot decrypt %s: hostkey secret not available",
            f"{account_dir.name}/{name}",
        )
        return None

    try:
        return f.decrypt(path.read_bytes()).decode("utf-8")
    except (InvalidToken, OSError) as exc:
        logger.warning("Failed to decrypt %s: %s", f"{account_dir.name}/{name}", exc)
        return None


def delete_secret_in(account_dir: Path, name: str) -> None:
    """Deletes the secret in the given account directory. Errors are ignored."""

    _delete_secret_at(account_dir / name)