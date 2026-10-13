"""Route for serving media files of the Anki collection.

Anki embeds media file links in card HTML as relative ``src="filename"``.
After rewriting to ``/ms/`` this route serves files from the
``collection.media/`` directory of the account bound to the cookie.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.storage.account import Account
from app.sync.media_files import safe_media_path
from app.web.deps import get_current_account

logger = logging.getLogger(__name__)
router = APIRouter()


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
        HTTPException: 404 if the file is missing, 400 if the path escapes
            ``collection.media/`` (NUL bytes, drive letters, ``..`` segments,
            or symlink traversal).
    """

    media_root = account.media_dir()
    file_path = safe_media_path(filename, media_root)
    if file_path is None:
        raise HTTPException(status_code=400, detail="Invalid path")

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Media not found")

    return FileResponse(
        file_path,
        headers = {
            "Cache-Control": "private, max-age=60", 
            "Vary": "Cookie"
        })
