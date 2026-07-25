"""Хранение секретов на диске через Fernet.

Файл `session.secret` создаётся лениво при первом обращении и используется
как ключ для шифрования/расшифровки прочих секретов (например, hostKey).
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
    """Возвращает путь к файлу с Fernet-ключом."""

    return get_settings().session_secret_file


def _ensure_data_dir() -> None:
    """Гарантирует существование родительского каталога для секретов."""

    path = _fernet_key_path()
    path.parent.mkdir(parents=True, exist_ok=True)


def _load_or_create_fernet_key() -> bytes:
    """Загружает Fernet-ключ из файла или создаёт новый.

    Файл создаётся с правами 0600.
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
    """Возвращает объект Fernet или None, если ключ ещё не создан.

    До первого login файл может отсутствовать — это нормально, не падаем.
    """

    path = _fernet_key_path()
    if not path.exists():
        return None
    try:
        return Fernet(path.read_bytes().strip())
    except (ValueError, OSError) as exc:
        logger.warning("Failed to load session secret: %s", exc)
        return None


def save_secret(name: str, value: str) -> None:
    """Шифрует ``value`` и сохраняет в ``<data_dir>/<name>`` (mode 0600).

    Args:
        name: имя файла (например, "hostkey.enc").
        value: открытый текст для шифрования.
    """

    settings = get_settings()
    f = _fernet() or Fernet(_load_or_create_fernet_key())
    encrypted = f.encrypt(value.encode("utf-8"))

    path = settings.data_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, DEFAULT_PERMISSIONS)
    try:
        os.write(fd, encrypted)
    finally:
        os.close(fd)
    logger.info("Saved secret %s", name)


def load_secret(name: str) -> str | None:
    """Расшифровывает и возвращает секрет, или None при отсутствии/ошибке.

    Args:
        name: имя файла секрета (например, "hostkey.enc").
    """

    settings = get_settings()
    path = settings.data_dir / name
    if not path.exists():
        return None

    f = _fernet()
    if f is None:
        logger.warning("Cannot decrypt %s: session secret not available", name)
        return None

    try:
        return f.decrypt(path.read_bytes()).decode("utf-8")
    except (InvalidToken, OSError) as exc:
        logger.warning("Failed to decrypt %s: %s", name, exc)
        return None


def delete_secret(name: str) -> None:
    """Удаляет файл секрета, если он существует. Ошибки игнорируются."""

    settings = get_settings()
    path = settings.data_dir / name
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Failed to delete %s: %s", name, exc)