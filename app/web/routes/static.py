"""Static asset serving under ``/static/``."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

@router.get("/static/{file_path:path}")
async def serve_static(file_path: str) -> FileResponse:
    """Serves a static asset with a short public cache.

    Args:
        file_path: relative path under ``app/web/static/``.

    Raises:
        HTTPException: 400 if the path escapes ``_STATIC_DIR``,
            404 if the file is missing.
    """

    if ".." in file_path.split("/") or file_path.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid path")

    target = (_STATIC_DIR / file_path).resolve()
    try:
        target.relative_to(_STATIC_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid path") from exc

    if not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")

    return FileResponse(
        target,
        headers={"Cache-Control": "public, max-age=3600, immutable"},
    )
