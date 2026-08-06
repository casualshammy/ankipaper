"""Local filesystem helpers for media sync.

Owns everything that touches the local ``collection.media/`` directory:

- extension filter (``SUPPORTED_EXTENSIONS``);
- path-traversal hardening (``safe_media_path``);
- zip extraction with per-file and collection-wide size limits
  (``extract_zip``);
- directory-size accounting for the collection-wide budget.

Knows nothing about AnkiWeb HTTP — see ``app.sync.media_protocol`` for
that side of the wire.
"""

from __future__ import annotations

import json
import logging
from stat import S_ISREG
import zipfile
from io import BytesIO
from pathlib import Path

logger = logging.getLogger(__name__)

#: Extensions worth downloading for an e-ink Kindle.
#:
#: - Images: rendered as card media (``<img src="...">``).
#: - Fonts: used by card templates via ``@font-face { src: url(...) }``.
#:
#: Audio, video, JS, etc. are filtered out — they are not rendered on
#: Kindle and only waste disk space.
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".jpg", ".jpeg", ".png", ".gif", ".webp",
        ".otf", ".ttf", ".woff", ".woff2",
    }
)

#: Streaming chunk size for media extraction. Small enough to keep
#: memory bounded, large enough to keep syscall overhead low.
_CHUNK_SIZE = 64 * 1024

#: Name of the manifest entry inside a media zip.
_META_ENTRY = "_meta"


def media_dir(data_dir: Path) -> Path:
    """Returns the path to the media files directory of the collection."""

    return data_dir / "collection.media"


def media_dir_size(media_dir: Path) -> int:
    """Returns the total size in bytes of all regular files under ``media_dir``.

    Missing directories count as 0. ``Path.rglob`` does not descend into
    symlinked directories; for individual files ``is_file`` follows
    symlinks by default, matching the original ``os.walk`` semantics.
    """

    if not media_dir.exists():
        return 0
    total = 0
    for entry in media_dir.rglob("*"):
        try:
            fileStat = entry.stat()
            if S_ISREG(fileStat.st_mode):
                total += fileStat.st_size
        except OSError:
            # File removed between rglob and stat — ignore.
            continue
    return total


def is_supported(fname: str) -> bool:
    """True if ``fname`` has an extension we want to download.

    Check is case-insensitive; we look at the **last** suffix, so
    ``foo.tar.gz`` is filtered out as ``.gz`` (not supported), and
    ``image.JPG.bak`` as ``.bak``. AnkiWeb always stores names in NFC
    with a single extension, so in practice this is a simple check.
    """

    return Path(fname).suffix.lower() in SUPPORTED_EXTENSIONS


def safe_media_path(real_name: str, target_dir: Path) -> Path | None:
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
    if normalised.startswith(("/", "\\")):
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

    # Defence in depth: refuse to traverse any existing symlink under
    # ``target_dir``. ``ZipFile.open`` never produces symlinks, but a
    # previously extracted media file could be one pointing outside the
    # media directory.
    for ancestor in target.parents:
        if not ancestor.is_relative_to(target_dir_resolved):
            break
        if ancestor.is_symlink():
            return None
    return target


def extract_zip(
    zip_bytes: bytes,
    target_dir: Path,
    *,
    max_file_bytes: int | None,
    remaining_budget: int | None,
) -> tuple[list[str], int, int, bool]:
    """Extracts a media zip into ``target_dir``.

    Zip format (see ``_anki_repo/rslib/src/sync/media/zip.rs:29-48``):
    - files are named by index as a string: ``"0"``, ``"1"``, ...;
    - the zip contains ``_meta`` — a JSON dict ``{idx_str: real_name}``
      where ``real_name`` is the actual file name.

    The declared size in the zip central directory (``info.file_size``)
    is **not trusted** for budget enforcement. A crafted zip can lie
    about the size while delivering a much larger payload. Per-file and
    collection-wide limits are therefore enforced against actual bytes
    during the streaming write; the header is only used as an advisory
    pre-filter, and a mismatch between header and actual size is logged.

    Args:
        zip_bytes: zip contents from the server.
        target_dir: where to extract (e.g. ``/data/collection.media``).
        max_file_bytes: if not None, individual files larger than this
            are skipped with a warning and counted in ``oversize_count``.
        remaining_budget: if not None, the remaining bytes available in
            the user's collection before hitting the collection-size
            limit. Files that would exceed it are not written; once the
            budget is exhausted, ``hit_collection_limit`` is set.

    Returns:
        Tuple ``(extracted_names, bytes_written, oversize_count,
        hit_collection_limit)``.
    """

    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        meta = _read_meta(zf)
        return _extract_entries(
            zf, meta, target_dir,
            max_file_bytes=max_file_bytes,
            remaining_budget=remaining_budget,
        )


def _read_meta(zf: zipfile.ZipFile) -> dict[str, str]:
    """Returns the ``_meta`` mapping from a zip, or empty dict if absent."""

    if _META_ENTRY not in zf.namelist():
        return {}
    with zf.open(_META_ENTRY) as f:
        return json.loads(f.read().decode("utf-8"))


def _extract_entries(
    zf: zipfile.ZipFile,
    meta: dict[str, str],
    target_dir: Path,
    *,
    max_file_bytes: int | None,
    remaining_budget: int | None,
) -> tuple[list[str], int, int, bool]:
    """Walks the zip entries and accumulates extraction results."""

    extracted: list[str] = []
    bytes_written = 0
    oversize_count = 0
    hit_collection_limit = False

    for info in zf.infolist():
        if info.filename == _META_ENTRY or info.is_dir():
            continue

        real_name = meta.get(info.filename, info.filename)
        # Advisory pre-filter on the declared size. The header lies —
        # the real limit is enforced below against actual bytes.
        if max_file_bytes is not None and info.file_size > max_file_bytes:
            logger.warning(
                "Skipping oversize media file: %r size=%d > %d",
                real_name, info.file_size, max_file_bytes,
            )
            oversize_count += 1
            continue

        outcome = _extract_entry(
            zf, info, real_name, target_dir,
            max_file_bytes=max_file_bytes,
            remaining_budget=(
                remaining_budget - bytes_written
                if remaining_budget is not None
                else None
            ),
        )

        if outcome.kind == "skipped":
            oversize_count += 1
        elif outcome.kind == "budget_hit":
            hit_collection_limit = True
            # No point processing more entries — collection is full.
            break
        else:
            extracted.append(real_name)
            bytes_written += outcome.bytes_written
            if info.file_size and outcome.bytes_written != info.file_size:
                logger.warning(
                    "Media zip entry size mismatch: %r header=%d actual=%d",
                    real_name, info.file_size, outcome.bytes_written,
                )

    return extracted, bytes_written, oversize_count, hit_collection_limit


class _EntryOutcome:
    """Result of extracting a single zip entry."""

    __slots__ = ("kind", "bytes_written")

    def __init__(self, kind: str, bytes_written: int) -> None:
        self.kind = kind
        self.bytes_written = bytes_written


def _extract_entry(
    zf: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    real_name: str,
    target_dir: Path,
    *,
    max_file_bytes: int | None,
    remaining_budget: int | None,
) -> _EntryOutcome:
    """Streams a single zip entry to disk with limit enforcement."""

    target = safe_media_path(real_name, target_dir)
    if target is None:
        logger.warning(
            "Skipping media entry with unsafe filename: %r (zip name: %s)",
            real_name, info.filename,
        )
        return _EntryOutcome("skipped", 0)

    target.parent.mkdir(parents=True, exist_ok=True)
    actual_size = 0
    size_exceeded = False
    budget_exceeded = False

    with zf.open(info) as src, target.open("wb") as dst:
        while True:
            chunk = src.read(_CHUNK_SIZE)
            if not chunk:
                break
            actual_size += len(chunk)
            if max_file_bytes is not None and actual_size > max_file_bytes:
                size_exceeded = True
                break
            if remaining_budget is not None and actual_size > remaining_budget:
                budget_exceeded = True
                break
            dst.write(chunk)

    if size_exceeded or budget_exceeded:
        safe_unlink(target)
        if size_exceeded:
            logger.warning(
                "Media file exceeds max_file_bytes during write: %r actual=%d > %d",
                real_name, actual_size, max_file_bytes,
            )
            return _EntryOutcome("skipped", 0)
        logger.warning(
            "Media collection size limit reached during write: %r "
            "actual=%d > remaining=%d",
            real_name, actual_size, remaining_budget,
        )
        return _EntryOutcome("budget_hit", 0)

    return _EntryOutcome("written", actual_size)


def safe_unlink(path: Path) -> bool:
    """Removes ``path`` and returns True on success or a missing file.

    Returns False and logs an ``OSError`` traceback if the unlink fails
    for any other reason (permission denied, file in use, etc.).
    """

    try:
        path.unlink()
    except FileNotFoundError:
        return True
    except OSError:
        logger.exception("Failed to delete media file: %r", path)
        return False
    return True