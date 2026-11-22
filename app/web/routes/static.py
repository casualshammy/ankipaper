"""Static asset serving under ``/static/``."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

router = APIRouter()

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_VERSIONED_CACHE_CONTROL = "public, max-age=31536000, immutable"
_PLAIN_CACHE_CONTROL = "public, max-age=60, must-revalidate"


@router.get("/static/{file_path:path}")
async def serve_static(file_path: str, request: Request) -> FileResponse:
    """Serves a static asset with a public cache.

    Versioned requests (with a non-empty ``?v=`` query parameter) are
    cached immutably; unversioned ones are revalidated after a minute.

    Args:
        file_path: relative path under ``app/web/static/``.
        request: incoming request, used to read the ``v`` cache-busting key.

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

    version_key = request.query_params.get("v", "").strip()
    cache_control = _VERSIONED_CACHE_CONTROL if version_key else _PLAIN_CACHE_CONTROL

    return FileResponse(
        target,
        headers={"Cache-Control": cache_control},
    )
