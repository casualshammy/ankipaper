"""Route for serving media files of the Anki collection.

Anki embeds media file links in card HTML as relative ``src="filename"``.
After rewriting to ``/ms/`` this route serves files from the
``collection.media/`` directory of the account bound to the cookie.
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
    """Returns the path to the media files directory of the account."""

    return account.media_dir()


@router.get("/ms/{filename:path}")
async def serve_media(
    filename: str,
    account: Account = Depends(get_current_account),
) -> FileResponse:
    """Serves a media file from ``collection.media/`` of the current account.

    Args:
        filename: relative path inside ``collection.media/`` (e.g. ``myimage.jpg``).
        account: current account (from the cookie).

    Raises:
        HTTPException: 404 if the file is missing, 400 if the path contains ``..``.
    """

    if ".." in filename.split("/") or filename.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid path")

    media_root = _media_dir(account)
    file_path = (media_root / filename).resolve()

    # Path-traversal protection: file_path must remain inside media_root.
    try:
        file_path.relative_to(media_root.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid path") from exc

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Media not found")

    return FileResponse(
        file_path,
        headers={
            "Cache-Control": "private, max-age=3600",
            "Vary": "Cookie",
        },
    )
