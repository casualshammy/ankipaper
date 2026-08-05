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

logger = logging.getLogger(__name__)

# Mode 0600 for all secret files on disk.
_SECRET_FILE_MODE = 0o600

# All instance-wide secrets live under /data; per-account secrets live
# under /data/accounts/<id>/ and use the dedicated hostkey Fernet.
_DATA_DIR = Path("/data")
_SESSION_SECRET_PATH = _DATA_DIR / "session.secret"
_HOSTKEY_SECRET_PATH = _DATA_DIR / "hostkey.secret"


# --- Low-level helpers shared by both Fernet key pipelines ---


def _load_or_create_key_bytes(path: Path, *, log_label: str) -> bytes:
    """Returns the Fernet key bytes stored at ``path``, creating the file if missing.

    The file is created with mode 0600. ``log_label`` is the human-readable
    name used in the "generated" log line (e.g. ``"session secret"``).
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path.read_bytes().strip()

    key = Fernet.generate_key()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _SECRET_FILE_MODE)
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    logger.info("Generated new %s at %s", log_label, path)
    return key


def _fernet_from(path: Path) -> Fernet | None:
    """Returns a Fernet instance for the key file at ``path``, or None.

    Returns ``None`` if the file is missing or unreadable. Callers must
    treat ``None`` as "no secret available".
    """

    if not path.exists():
        return None
    try:
        return Fernet(path.read_bytes().strip())
    except (ValueError, OSError) as exc:
        logger.warning("Failed to load secret at %s: %s", path, exc)
        return None


def _encrypt_and_write(path: Path, plaintext: str, fernet: Fernet, *, name_for_log: str) -> None:
    """Encrypts ``plaintext`` with ``fernet`` and writes the ciphertext to ``path`` (mode 0600)."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _SECRET_FILE_MODE)
    try:
        os.write(fd, fernet.encrypt(plaintext.encode("utf-8")))
    finally:
        os.close(fd)
    logger.info("Saved secret %s", name_for_log)


def _decrypt_or_none(path: Path, fernet: Fernet, *, name_for_log: str) -> str | None:
    """Reads ``path`` and decrypts it with ``fernet``; logs and returns ``None`` on any error."""

    try:
        return fernet.decrypt(path.read_bytes()).decode("utf-8")
    except (InvalidToken, OSError) as exc:
        logger.warning("Failed to decrypt %s: %s", name_for_log, exc)
        return None


def _safe_unlink(path: Path) -> None:
    """Removes ``path`` if it exists; logs and ignores OS errors."""

    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Failed to delete %s: %s", path, exc)


# --- Session secret (cookie + CSRF) ---


def _load_or_create_fernet_key() -> bytes:
    """Returns the ``session.secret`` Fernet key bytes, creating the file if missing."""

    return _load_or_create_key_bytes(_SESSION_SECRET_PATH, log_label="session secret")


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

    if not _SESSION_SECRET_PATH.exists():
        return None
    try:
        return _SESSION_SECRET_PATH.read_bytes().strip()
    except OSError as exc:
        logger.warning("Cannot read session secret: %s", exc)
        return None


# --- hostKey secret (per-account encryption) ---


def _load_or_create_hostkey_fernet_key() -> bytes:
    """Returns the ``hostkey.secret`` Fernet key bytes, creating the file if missing."""

    return _load_or_create_key_bytes(_HOSTKEY_SECRET_PATH, log_label="hostkey secret")


def _hostkey_fernet() -> Fernet | None:
    """Returns a Fernet instance for hostKey encryption, or ``None`` if missing."""

    return _fernet_from(_HOSTKEY_SECRET_PATH)


# --- Per-account secret save/load/delete ---


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
    fernet = _hostkey_fernet() or Fernet(_load_or_create_hostkey_fernet_key())
    _encrypt_and_write(path, value, fernet, name_for_log=f"{account_dir.name}/{name}")


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

    fernet = _hostkey_fernet()
    if fernet is None:
        logger.warning(
            "Cannot decrypt %s: hostkey secret not available",
            f"{account_dir.name}/{name}",
        )
        return None
    return _decrypt_or_none(path, fernet, name_for_log=f"{account_dir.name}/{name}")


def delete_secret_in(account_dir: Path, name: str) -> None:
    """Deletes the secret in the given account directory. Errors are ignored."""

    _safe_unlink(account_dir / name)
