"""Direct media-file synchronisation with AnkiWeb via HTTP.

Bypasses the Rust backend ``col.sync_media``, which in our environment
fails with ``BackendIOError: Failed to create file in '<col>': File exists``.
Implements the media sync v11 protocol (zstd + JSON) directly via HTTP,
following the reference ``_anki_repo/rslib/src/sync/http_server/handlers.rs``
and ``_anki_repo/rslib/src/sync/http_client/protocol.rs``.

This module is the orchestrator — it ties together the wire protocol
(``app.sync.media_protocol``) and the local filesystem helpers
(``app.sync.media_files``). The public surface is:

- :func:`sync_media_direct` — run a full incremental media sync;
- :class:`SyncHttpError` — re-exported from ``media_protocol`` for
  backward compatibility with the original ``media_http`` module.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

from app.sync.endpoints import normalize_endpoint
from app.sync.media_files import (
    extract_zip,
    is_supported,
    media_dir,
    media_dir_size,
    safe_media_path,
    safe_unlink,
)
from app.sync.media_protocol import (
    SyncHttpError,
    USER_AGENT,
    decode_response,
    decompress_if_zstd,
    make_session_key,
    post_json,
)

# Re-export for callers that import ``SyncHttpError`` from this module.
__all__ = ["sync_media_direct", "SyncHttpError"]

logger = logging.getLogger(__name__)

#: Max entries per ``mediaChanges`` response (matches the server-side SQL
#: limit — see ``_anki_repo/rslib/src/sync/media/database/server/entry/changes.sql``).
_MEDIA_CHANGES_BATCH = 1000

ProgressCallback = Callable[[str, int, int, int], None]


def sync_media_direct(
    *,
    host_key: str,
    endpoint: str | None,
    data_dir: Path,
    last_usn_path: Path,
    batch_limit: int = 25,
    image_only: bool = True,
    progress_callback: ProgressCallback | None = None,
    max_file_bytes: int | None = None,
    max_collection_bytes: int | None = None,
) -> dict:
    """Downloads media files from AnkiWeb into ``data_dir/collection.media``.

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
        max_file_bytes: if not None, individual files larger than this
            are skipped (with a warning) and not written to disk.
        max_collection_bytes: if not None, the total on-disk size of
            ``collection.media/`` is bounded by this value. When the
            existing directory is already at or above the limit, no new
            files are written and ``collection_too_large`` in the
            returned dict is set to True.

    Returns:
        Dict with the result: ``{"downloaded": N, "total": N,
        "skipped": N, "skipped_oversize": N, "deleted": N,
        "delete_errors": N, "bytes_freed": N, "last_usn": N,
        "endpoint": "...", "collection_too_large": bool}``.
    """

    base = normalize_endpoint(endpoint)
    media = media_dir(data_dir)
    # A single session_key for the whole session — AnkiWeb tracks state
    # by it. See ``_anki_repo/rslib/src/sync/http_client/mod.rs:41``.
    session_key = make_session_key()

    logger.info("Media sync (direct HTTP): endpoint=%s", base)

    server_usn = _begin_session(base, host_key, session_key)
    logger.info("Media sync: server_usn=%s", server_usn)

    last_usn = _read_last_usn(last_usn_path)
    all_files, to_delete, skipped_unsupported = _collect_changes(
        base, host_key, session_key, last_usn, server_usn,
        image_only, progress_callback,
    )
    _emit_progress(progress_callback, "mediaChanges", server_usn, server_usn, 0)

    downloaded, bytes_written, skipped_oversize, collection_too_large = _download_all(
        base, host_key, session_key, all_files, media,
        batch_limit=batch_limit,
        max_file_bytes=max_file_bytes,
        max_collection_bytes=max_collection_bytes,
        progress_callback=progress_callback,
    )

    deleted, deleted_bytes, delete_errors = _apply_deletions(to_delete, media)

    # Persist ``last_usn`` even when the collection hit the size limit —
    # server-side state has already advanced past these files, and we
    # don't want to redownload them on every sync.
    last_usn_path.parent.mkdir(parents=True, exist_ok=True)
    last_usn_path.write_text(str(server_usn))

    logger.info(
        "Media sync complete: %d files downloaded, %d deleted, %d unsupported "
        "skipped, %d oversize skipped, %d bytes written, "
        "collection_too_large=%s, last_usn=%s",
        len(downloaded), deleted, skipped_unsupported, skipped_oversize,
        bytes_written, collection_too_large, server_usn,
    )

    return {
        "downloaded": len(downloaded),
        "total": len(all_files),
        "skipped": skipped_unsupported,
        "skipped_oversize": skipped_oversize,
        "deleted": deleted,
        "delete_errors": delete_errors,
        "bytes_freed": deleted_bytes,
        "bytes_written": bytes_written,
        "collection_too_large": collection_too_large,
        "last_usn": server_usn,
        "endpoint": base,
    }


# ---------------------------------------------------------------------------
# Phase helpers
# ---------------------------------------------------------------------------


def _begin_session(base: str, host_key: str, session_key: str) -> int:
    """Calls ``/msync/begin`` and returns the server's current USN."""

    raw = post_json(base, "begin", host_key, {"v": USER_AGENT}, session_key)
    begin = decode_response(raw)
    if not isinstance(begin, dict):
        raise SyncHttpError(f"Unexpected begin response: {begin!r}")
    return int(begin["usn"])


def _read_last_usn(last_usn_path: Path) -> int:
    """Reads the persisted USN, falling back to 0 on missing/corrupt files."""

    if not last_usn_path.exists():
        return 0
    try:
        return int(last_usn_path.read_text().strip())
    except ValueError:
        return 0


def _collect_changes(
    base: str,
    host_key: str,
    session_key: str,
    last_usn: int,
    server_usn: int,
    image_only: bool,
    progress_callback: ProgressCallback | None,
) -> tuple[list[tuple[str, str]], list[str], int]:
    """Paginates ``/msync/mediaChanges`` and partitions entries by action.

    Args:
        server_usn: server-side ceiling for the progress bar's ``total``.

    Returns:
        ``(all_files, to_delete, skipped_unsupported)`` where ``all_files``
        is the list of ``(fname, sha1)`` pairs to download,
        ``to_delete`` is the list of filenames deleted on AnkiWeb, and
        ``skipped_unsupported`` counts entries filtered out by the
        extension whitelist.
    """

    all_files: list[tuple[str, str]] = []
    to_delete: list[str] = []
    skipped_unsupported = 0

    while True:
        raw = post_json(
            base, "mediaChanges", host_key, {"lastUsn": last_usn}, session_key,
        )
        changes = decode_response(raw)
        if not isinstance(changes, list):
            raise SyncHttpError(f"Unexpected mediaChanges response: {changes!r}")
        if not changes:
            break

        for entry in changes:
            fname, skipped_unsupported, to_delete = _classify_change(
                entry, image_only, skipped_unsupported, to_delete,
            )
            if fname is not None:
                all_files.append(fname)

        last_usn = int(changes[-1][1])
        logger.info(
            "Media sync: %d entries in this batch, next last_usn=%d (total so far: %d)",
            len(changes), last_usn, len(all_files),
        )
        _emit_progress(
            progress_callback, "mediaChanges",
            int(last_usn), max(int(last_usn), int(server_usn)), 0,
        )
        if len(changes) < _MEDIA_CHANGES_BATCH:
            # Less than the limit — this was the last batch.
            break

    return all_files, to_delete, skipped_unsupported


def _classify_change(
    entry: list,
    image_only: bool,
    skipped_unsupported: int,
    to_delete: list[str],
) -> tuple[tuple[str, str] | None, int, list[str]]:
    """Classifies one ``mediaChanges`` row.

    Returns ``(file_pair_or_none, skipped_unsupported, to_delete)`` so the
    caller can fold counters back into the running totals. AnkiWeb
    serialises ``MediaChange`` as ``[fname, usn, sha1]`` (tuple, not
    object — see ``_anki_repo/rslib/src/sync/media/protocol.rs``).
    """

    if not isinstance(entry, list) or len(entry) < 3:
        logger.warning("Skipping malformed media change entry: %r", entry)
        return None, skipped_unsupported, to_delete

    fname, _usn, sha1 = entry[0], entry[1], entry[2]
    if not sha1:
        # Empty sha1 means "deleted on AnkiWeb" — defer the local unlink
        # until after downloads so a download failure does not strand
        # already-deleted entries that we'd then have to re-process.
        to_delete.append(fname)
        return None, skipped_unsupported, to_delete
    if image_only and not is_supported(fname):
        return None, skipped_unsupported + 1, to_delete
    return (fname, sha1), skipped_unsupported, to_delete


def _download_all(
    base: str,
    host_key: str,
    session_key: str,
    all_files: list[tuple[str, str]],
    media: Path,
    *,
    batch_limit: int,
    max_file_bytes: int | None,
    max_collection_bytes: int | None,
    progress_callback: ProgressCallback | None,
) -> tuple[list[str], int, int, bool]:
    """Downloads every entry in ``all_files`` in batches of ``batch_limit``."""

    downloaded: list[str] = []
    bytes_written = 0
    skipped_oversize = 0
    total_files = len(all_files)

    # Compute the existing on-disk size once so we can enforce the
    # collection-wide budget without re-scanning the directory per file.
    base_size = media_dir_size(media) if max_collection_bytes is not None else 0
    collection_too_large = (
        max_collection_bytes is not None and base_size >= max_collection_bytes
    )
    if collection_too_large:
        logger.warning(
            "Media collection size limit reached before download: "
            "current=%d limit=%d — new files will not be written",
            base_size, max_collection_bytes,
        )

    for i in range(0, total_files, batch_limit):
        if collection_too_large:
            # Already over the limit — skip remaining batches entirely;
            # there is no point downloading zips we will not extract.
            break

        batch = [f for f, _ in all_files[i : i + batch_limit]]
        if not batch:
            continue

        _log_batch_start(i, batch, total_files)
        zip_bytes = _fetch_zip(base, host_key, session_key, batch)
        remaining_budget = (
            max_collection_bytes - base_size - bytes_written
            if max_collection_bytes is not None
            else None
        )
        extracted, written_now, oversize_now, hit_limit = extract_zip(
            zip_bytes, media,
            max_file_bytes=max_file_bytes,
            remaining_budget=remaining_budget,
        )
        downloaded.extend(extracted)
        bytes_written += written_now
        skipped_oversize += oversize_now
        if hit_limit:
            collection_too_large = True
            logger.warning(
                "Media collection size limit reached during extraction: "
                "base=%d written=%d limit=%d",
                base_size, bytes_written, max_collection_bytes,
            )

        _emit_progress(
            progress_callback, "downloadFiles",
            min(i + len(batch), total_files), max(total_files, 1),
            len(downloaded),
        )

    return downloaded, bytes_written, skipped_oversize, collection_too_large


def _log_batch_start(i: int, batch: list[str], total: int) -> None:
    """Logs the start of a download batch (with first-payload debug)."""

    logger.info(
        "Downloading media batch %d-%d/%d (sample: %r)",
        i, i + len(batch), total, batch[:3],
    )
    if i == 0:
        # Log the full payload of the first request for 400 debugging.
        try:
            logger.debug("downloadFiles payload: %s", json.dumps({"files": batch})[:500])
        except Exception:  # noqa: BLE001
            pass


def _fetch_zip(
    base: str,
    host_key: str,
    session_key: str,
    batch: list[str],
) -> bytes:
    """Fetches a ``downloadFiles`` batch and returns the raw zip bytes.

    The server returns a zstd-compressed zip body — ``decode_response``
    cannot help here because it falls back to the still-compressed raw
    bytes when JSON parsing fails (a zip isn't JSON).
    """

    raw = post_json(
        base, "downloadFiles", host_key, {"files": batch}, session_key,
    )
    return decompress_if_zstd(raw)


def _apply_deletions(
    to_delete: list[str],
    media: Path,
) -> tuple[int, int, int]:
    """Removes files AnkiWeb has deleted, via :func:`safe_media_path`."""

    deleted = 0
    deleted_bytes = 0
    delete_errors = 0
    for fname in to_delete:
        target = safe_media_path(fname, media)
        if target is None:
            logger.warning("Skipping media deletion with unsafe filename: %r", fname)
            delete_errors += 1
            continue

        size = _safe_stat_size(target)
        if safe_unlink(target):
            deleted += 1
            deleted_bytes += size
        else:
            # ``safe_unlink`` only returns False for OS errors other
            # than FileNotFoundError, which is what we count as an error.
            delete_errors += 1
    return deleted, deleted_bytes, delete_errors


def _safe_stat_size(path: Path) -> int:
    """Returns ``path``'s size, or 0 if it cannot be stated."""

    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0
    except OSError:
        logger.warning("Failed to stat media file before delete: %r", path)
        return 0


def _emit_progress(
    callback: ProgressCallback | None,
    phase: str,
    current: int,
    total: int,
    downloaded: int,
) -> None:
    """Invokes ``callback`` with defensive error handling."""

    if callback is None:
        return
    try:
        callback(phase, current, total, downloaded)
    except Exception:  # noqa: BLE001
        logger.exception("progress_callback raised during %s", phase)