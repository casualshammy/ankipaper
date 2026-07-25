"""Роут для раздачи медиа-файлов коллекции Anki.

Anki встраивает в HTML карточки ссылки на медиа-файлы через
``src="filename"`` (relative). После переписывания в ``/ms/<filename>``
этот роут отдаёт файлы из директории ``collection.media/`` аккаунта,
к которому привязан cookie.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.storage.account import Account
from app.web.deps import get_current_account

logger = logging.getLogger(__name__)

router = APIRouter()


def _media_dir(account: Account) -> Path:
    """Возвращает путь к директории медиа-файлов аккаунта."""

    return account.media_dir()


@router.get("/ms/{filename:path}")
async def serve_media(
    filename: str,
    account: Account = Depends(get_current_account),
) -> FileResponse:
    """Отдаёт медиа-файл из ``collection.media/`` текущего аккаунта.

    Args:
        filename: относительный путь внутри ``collection.media/``
            (например, ``myimage.jpg``).
        account: текущий аккаунт (из cookie).

    Raises:
        HTTPException: 404, если файла нет или путь содержит ``..``.
    """

    if ".." in filename.split("/") or filename.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid path")

    media_root = _media_dir(account)
    file_path = (media_root / filename).resolve()

    # Защита от path traversal: file_path должен остаться внутри media_root.
    try:
        file_path.relative_to(media_root.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid path") from exc

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Media not found")

    return FileResponse(file_path)
