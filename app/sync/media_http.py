"""Direct media file synchronisation with AnkiWeb via HTTP.

Bypasses the Rust backend ``col.sync_media``, which in our environment
fails with ``BackendIOError: Failed to create file in '<col>': File exists``.
Implements the media sync v3 protocol (zstd + JSON) directly via HTTP,
following the reference ``_anki_repo/rslib/src/sync/http_server/handlers.rs``
and ``_anki_repo/rslib/src/sync/http_client/protocol.rs``.

Endpoints (sync v11, zstd):
- ``POST {endpoint}/msync/begin``         — get server_usn
- ``POST {endpoint}/msync/mediaChanges`` — list of changes (camelCase JSON)
- ``POST {endpoint}/msync/downloadFiles`` — download a zip of files

Header: ``Anki-Sync: {"v": 11, "k": <hostkey>, "c": <client_ver>, "s": <session>}``
Body: zstd-compressed JSON.
Response: zstd-compressed, with the ``Anki-Original-Size`` header.

Important: all requests within a single ``sync_media_direct`` session
use the **same** ``session_key`` — AnkiWeb uses it to track the session.
"""

from __future__ import annotations

import json
import logging
import random
import string
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

import zstandard as zstd

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "https://sync.ankiweb.net/"
SYNC_VERSION = 11
ORIGINAL_SIZE_HEADER = "anki-original-size"
SYNC_HEADER_NAME = "anki-sync"
USER_AGENT = "kindlanki/0.1"

# Extensions worth downloading for an e-ink Kindle.
# - Images: rendered as card media (``<img src="...">``).
# - Fonts: used by card templates via ``@font-face { src: url(...) }``.
# Audio, video, JS, etc. are still filtered out — they are not rendered
# on Kindle and only waste disk space.
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".jpg", ".jpeg", ".png", ".gif", ".webp",
        ".otf", ".ttf", ".woff", ".woff2",
    }
)


def _endpoint(value: str | None) -> str:
    """Returns the sync endpoint (``sync20.ankiweb.net`` etc.)."""

    return (value or DEFAULT_ENDPOINT).rstrip("/") + "/"


def _compress(data: bytes) -> bytes:
    """Compresses ``data`` with zstd (format compatible with the AnkiWeb server)."""

    cctx = zstd.ZstdCompressor()
    return cctx.compress(data)


def _decompress(data: bytes) -> bytes:
    """Decompresses zstd data from the AnkiWeb server."""

    dctx = zstd.ZstdDecompressor()
    return dctx.decompress(data, max_output_size=512 * 1024 * 1024)


def _make_session_key() -> str:
    """Generates a pseudo-random session_key (format like AnkiDroid).

    AnkiDroid uses ``rand::random::<u32>`` plus base-N encoding
    (see ``_anki_repo/rslib/src/sync/http_client/mod.rs:109-113``).
    Here is a simple analogue: 16 random ASCII characters.
    """

    alphabet = string.ascii_letters + string.digits
    return "".join(random.choice(alphabet) for _ in range(16))


def _post_json(
    endpoint: str,
    method: str,
    host_key: str,
    payload: dict | list,
    session_key: str,
) -> bytes:
    """POSTs ``method`` to ``endpoint`` with a zstd-compressed JSON payload.

    Args:
        endpoint: base URL (e.g. ``https://sync20.ankiweb.net/``).
        method: method name (``begin``, ``mediaChanges``, ``downloadFiles``).
        host_key: user hostKey.
        payload: data serialisable as JSON.
        session_key: single session_key shared by the entire media-sync session.

    Returns:
        Raw (zstd-compressed) response body. Use ``_decode_response`` to
        decompress and check for errors.

    Raises:
        SyncHttpError: on a network error or HTTP 4xx/5xx.
    """

    # Media sync lives under ``/msync/*`` (see
    # ``_anki_repo/rslib/src/sync/http_server/mod.rs:248-249``),
    # the collection lives under ``/sync/*``.
    url = urllib.parse.urljoin(endpoint, f"msync/{method}")
    body = _compress(json.dumps(payload).encode("utf-8"))
    header = {
        "v": SYNC_VERSION,
        "k": host_key,
        "c": USER_AGENT,
        "s": session_key,
    }
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/octet-stream",
            SYNC_HEADER_NAME: json.dumps(header),
            "User-Agent": USER_AGENT,
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        body_bytes = exc.read()[:500]
        try:
            body_text = body_bytes.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            body_text = repr(body_bytes)
        logger.warning(
            "AnkiWeb %s failed: status=%d url=%s body=%s headers=%s",
            method,
            exc.code,
            url,
            body_text,
            dict(exc.headers.items()) if exc.headers else None,
        )
        raise SyncHttpError(
            f"AnkiWeb returned {exc.code} for {method}: {body_text or exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise SyncHttpError(f"Network error during {method}: {exc.reason}") from exc


def _decode_response(raw: bytes) -> dict | list | bytes:
    """Decodes a zstd response from the server and checks the ``JsonResult`` wrapper.

    Media-sync (``/msync/*``) wraps JSON responses in a ``JsonResult`` —
    an ``untagged`` enum: ``{"data": <T>, "err": ""}`` (Ok) or
    ``{"err": "..."}`` (Err). ``downloadFiles`` returns a raw zip.

    Args:
        raw: zstd-compressed response body.

    Returns:
        Decoded JSON object (for media sync) or raw bytes (for
        downloadFiles).
    """

    try:
        decompressed = _decompress(raw)
    except zstd.ZstdError:
        # Not zstd — maybe already decompressed, or not zstd at all.
        return raw

    try:
        wrapper = json.loads(decompressed)
    except json.JSONDecodeError:
        # Not JSON — this is a zip (downloadFiles) or another binary.
        return raw

    # ``JsonResult`` (see ``_anki_repo/rslib/src/sync/media/protocol.rs:71-80``):
    #   - Ok:   ``{"data": <T>, "err": ""}``
    #   - Err:  ``{"err": "..."}``
    if isinstance(wrapper, dict):
        if "err" in wrapper and isinstance(wrapper["err"], str) and wrapper["err"]:
            raise SyncHttpError(f"AnkiWeb sync error: {wrapper['err']}")
        if "data" in wrapper:
            return wrapper["data"]
    return wrapper


class SyncHttpError(RuntimeError):
    """Failure of an HTTP request to an AnkiWeb sync endpoint."""


def _media_dir(data_dir: Path) -> Path:
    """Returns the path to the media files directory of the collection."""

    return data_dir / "collection.media"


def _is_supported(fname: str) -> bool:
    """True if ``fname`` has an extension we want to download.

    Check is case-insensitive; we look at the **last** suffix, so
    ``foo.tar.gz`` is filtered out as ``.gz`` (not supported), and
    ``image.JPG.bak`` as ``.bak``. AnkiWeb always stores names in NFC
    with a single extension, so in practice this is a simple check.
    """

    suffix = Path(fname).suffix.lower()
    return suffix in SUPPORTED_EXTENSIONS


def _safe_media_path(real_name: str, target_dir: Path) -> Path | None:
    """Resolves ``real_name`` within ``target_dir`` without escape.

    Returns the absolute destination path if it is a regular file inside
    ``target_dir``. Returns ``None`` if the name is absolute, contains
    traversal components (``..``) or NUL bytes, points through a
    symlink, or otherwise escapes ``target_dir`` after resolution.

    Anki media names are arbitrary Unicode but never contain path
    separators or traversal segments in normal use — we reject anything
    that does.
    """

    if not real_name or "\x00" in real_name:
        return None
    normalised = real_name.replace("\\", "/")
    # Reject absolute paths (Unix ``/foo`` and Windows ``C:foo`` / ``\\host\share``).
    if normalised.startswith("/") or normalised.startswith("\\"):
        return None
    if len(normalised) >= 2 and normalised[1] == ":":
        return None
    parts = [p for p in normalised.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    target_dir_resolved = target_dir.resolve()
    target = target_dir.joinpath(*parts).resolve()
    try:
        target.relative_to(target_dir_resolved)
    except ValueError:
        return None
    # Defence in depth: refuse to traverse any existing symlink in the
    # path under ``target_dir``. ``zf.open()`` never produces symlinks,
    # but a previously extracted media file could be one pointing
    # outside the media directory.
    cur = target.parent
    while cur != cur.parent and cur.is_relative_to(target_dir_resolved):
        if cur.is_symlink():
            return None
        cur = cur.parent
    return target


def _extract_zip(zip_bytes: bytes, target_dir: Path) -> list[str]:
    """Extracts a media zip into ``target_dir``.

    Zip format (see ``_anki_repo/rslib/src/sync/media/zip.rs:29-48``):
    - files are named by index as a string: ``"0"``, ``"1"``, ...;
    - the zip contains ``_meta`` — a JSON dict ``{idx_str: real_name}``
      where ``real_name`` is the actual file name.

    Args:
        zip_bytes: zip contents from the server.
        target_dir: where to extract (e.g. ``/data/collection.media``).

    Returns:
        List of extracted file names.
    """

    target_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[str] = []

    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        meta: dict[str, str] = {}
        if "_meta" in zf.namelist():
            with zf.open("_meta") as f:
                meta = json.loads(f.read().decode("utf-8"))

        for info in zf.infolist():
            if info.filename == "_meta" or info.is_dir():
                continue
            real_name = meta.get(info.filename, info.filename)
            target = _safe_media_path(real_name, target_dir)
            if target is None:
                logger.warning(
                    "Skipping media entry with unsafe filename: %r (zip name: %s)",
                    real_name,
                    info.filename,
                )
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                dst.write(src.read())
            extracted.append(real_name)

    return extracted


def sync_media_direct(
    *,
    host_key: str,
    endpoint: str | None,
    data_dir: Path,
    last_usn_path: Path,
    batch_limit: int = 25,
    image_only: bool = True,
    progress_callback=None,
) -> dict:
    """Downloads media files from AnkiWeb and saves them to ``data_dir/collection.media``.

    Args:
        host_key: valid user hostKey.
        endpoint: sync server URL (``sync20.ankiweb.net`` etc.) or
            ``None`` for default.
        data_dir: directory containing ``collection.anki21``; the
            ``collection.media/`` directory is also written here.
        last_usn_path: path to the file storing the last processed
            ``server_usn`` (used for the next incremental sync).
        batch_limit: max files per ``downloadFiles`` request.
        image_only: if True (default), only download files with supported
            extensions — images (``.jpg``, ``.jpeg``, ``.png``, ``.gif``,
            ``.webp``) and fonts (``.otf``, ``.ttf``, ``.woff``,
            ``.woff2``). Audio, video, JS, etc. are skipped.
        progress_callback: optional callback ``(phase, current, total,
            downloaded) -> None``. ``phase`` is ``"mediaChanges"`` or
            ``"downloadFiles"``; ``current``/``total`` is the progress in
            the current phase; ``downloaded`` is how many files have
            already been downloaded. Used for the progress bar in the UI.

    Returns:
        Dict with the result: ``{"downloaded": N, "total": N,
        "skipped": N, "last_usn": N, "endpoint": "..."}``.
    """

    base = _endpoint(endpoint)
    media_dir = _media_dir(data_dir)
    # A single session_key for the whole session — AnkiWeb tracks state
    # by it. See ``_anki_repo/rslib/src/sync/http_client/mod.rs:41``.
    session_key = _make_session_key()

    logger.info("Media sync (direct HTTP): endpoint=%s", base)

    # 1. begin
    raw = _post_json(base, "begin", host_key, {"v": USER_AGENT}, session_key)
    begin = _decode_response(raw)
    if not isinstance(begin, dict):
        raise SyncHttpError(f"Unexpected begin response: {begin!r}")
    server_usn = int(begin["usn"])
    logger.info("Media sync: server_usn=%s", server_usn)

    # 2. mediaChanges (incremental). The server returns up to 1000 files
    #    per call, starting with ``usn > after_usn`` (see
    #    ``_anki_repo/rslib/src/sync/media/database/server/entry/changes.sql``).
    #    ``MediaChange`` is serialised via ``#[derive(Serialize_tuple)]`` —
    #    it's an array ``[fname, usn, sha1]``, not an object.
    last_usn = 0
    if last_usn_path.exists():
        try:
            last_usn = int(last_usn_path.read_text().strip())
        except ValueError:
            last_usn = 0

    all_files: list[tuple[str, str]] = []  # (fname, sha1)
    skipped_unsupported = 0
    while True:
        raw = _post_json(
            base, "mediaChanges", host_key, {"lastUsn": last_usn}, session_key
        )
        changes = _decode_response(raw)
        if not isinstance(changes, list):
            raise SyncHttpError(f"Unexpected mediaChanges response: {changes!r}")
        if not changes:
            break
        for c in changes:
            if not isinstance(c, list) or len(c) < 3:
                logger.warning("Skipping malformed media change entry: %r", c)
                continue
            fname, _entry_usn, sha1 = c[0], c[1], c[2]
            if not sha1:
                continue
            if image_only and not _is_supported(fname):
                skipped_unsupported += 1
                continue
            all_files.append((fname, sha1))
        # The ``usn`` of the last entry is the next ``last_usn`` for pagination.
        last_usn = int(changes[-1][1])
        logger.info(
            "Media sync: %d entries in this batch, next last_usn=%d (total so far: %d)",
            len(changes),
            last_usn,
            len(all_files),
        )
        if progress_callback is not None:
            # At this stage ``total`` is not yet known (it will be
            # ``len(all_files)`` after the last batch). Report progress
            # relative to what we've already seen; the UI will show an
            # indeterminate indicator.
            try:
                progress_callback(
                    "mediaChanges",
                    int(last_usn),
                    max(int(last_usn), int(server_usn)),
                    0,
                )
            except Exception:  # noqa: BLE001
                logger.exception("progress_callback raised during mediaChanges")
        if len(changes) < 1000:
            # Less than the limit — this was the last batch.
            break

    # The total number of files is now known — report the final ``total``
    # value for the downloadFiles phase.
    if progress_callback is not None:
        try:
            progress_callback(
                "mediaChanges",
                int(server_usn),
                int(server_usn),
                0,
            )
        except Exception:  # noqa: BLE001
            logger.exception("progress_callback raised at mediaChanges end")

    # 3. downloadFiles — in batches of batch_limit. The server returns a
    #    zip with the extracted files.
    downloaded: list[str] = []
    total_files = len(all_files)
    for i in range(0, total_files, batch_limit):
        batch = [f for f, _ in all_files[i : i + batch_limit]]
        if not batch:
            continue
        logger.info(
            "Downloading media batch %d-%d/%d (sample: %r)",
            i,
            i + len(batch),
            len(all_files),
            batch[:3],
        )
        # Log the full payload of the first request for 400 debugging.
        if i == 0:
            try:
                debug_payload = json.dumps({"files": batch})[:500]
                logger.debug("downloadFiles payload: %s", debug_payload)
            except Exception:  # noqa: BLE001
                pass
        raw = _post_json(
            base, "downloadFiles", host_key, {"files": batch}, session_key
        )
        # The server wraps the zip in zstd, like the other responses.
        if raw[:4] == b"\x28\xb5\x2f\xfd":  # zstd magic number
            zip_bytes = _decompress(raw)
        else:
            zip_bytes = raw
        downloaded.extend(_extract_zip(zip_bytes, media_dir))

        if progress_callback is not None:
            try:
                progress_callback(
                    "downloadFiles",
                    min(i + len(batch), total_files),
                    max(total_files, 1),
                    len(downloaded),
                )
            except Exception:  # noqa: BLE001
                logger.exception("progress_callback raised during downloadFiles")

    # Save last_usn for the next incremental sync.
    last_usn_path.parent.mkdir(parents=True, exist_ok=True)
    last_usn_path.write_text(str(server_usn))

    logger.info(
        "Media sync complete: %d files downloaded, %d unsupported skipped, last_usn=%s",
        len(downloaded),
        skipped_unsupported,
        server_usn,
    )

    return {
        "downloaded": len(downloaded),
        "total": len(all_files),
        "skipped": skipped_unsupported,
        "last_usn": server_usn,
        "endpoint": base,
    }

