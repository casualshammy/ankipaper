"""Storing secrets on disk via Fernet.

The `session.secret` file is created lazily on first access and is used
as the key for encrypting/decrypting other secrets (e.g. the hostKey).
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
    """Encrypts ``value`` and saves it to ``<account_dir>/<name>`` (mode 0600).

    Args:
        account_dir: account directory (``data/accounts/<id>``).
        name: file name (e.g. ``"hostkey.enc"``).
        value: plaintext value to encrypt.
    """

    _save_secret_at(account_dir / name, value, name_for_log=f"{account_dir.name}/{name}")


def load_secret_in(account_dir: Path, name: str) -> str | None:
    """Decrypts the secret from ``<account_dir>/<name>``, or None.

    Args:
        account_dir: account directory (``data/accounts/<id>``).
        name: secret file name.
    """

    return _load_secret_at(
        account_dir / name,
        name_for_log=f"{account_dir.name}/{name}",
    )


def delete_secret_in(account_dir: Path, name: str) -> None:
    """Deletes the secret in the given account directory. Errors are ignored."""

    _delete_secret_at(account_dir / name)