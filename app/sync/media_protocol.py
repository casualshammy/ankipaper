"""AnkiWeb media-sync wire-protocol helpers.

This module owns the HTTP/JSON/zstd details of talking to the AnkiWeb
``/msync/*`` endpoints:

- request serialisation (zstd + JSON body, ``Anki-Sync`` header);
- response deserialisation (zstd + ``JsonResult`` wrapper);
- session-key generation.

It deliberately knows nothing about zip extraction, the local media
directory layout, or the ``sync_media_direct`` orchestration. Callers
that need to read/write files on disk use ``app.sync.media_files``.

See ``_anki_repo/rslib/src/sync/http_server/mod.rs:248-249`` for the
``/msync`` vs ``/sync`` split and
``_anki_repo/rslib/src/sync/media/protocol.rs:71-80`` for the
``JsonResult`` envelope.
"""

from __future__ import annotations

import json
import logging
import random
import string
import urllib.error
import urllib.parse
import urllib.request

import zstandard as zstd

logger = logging.getLogger(__name__)

#: Protocol version sent in the ``Anki-Sync`` header (v11 = zstd body).
SYNC_VERSION = 11

#: Header carrying the JSON metadata for every sync request.
SYNC_HEADER_NAME = "anki-sync"

#: User-Agent value sent on every request and inside the ``Anki-Sync``
#: JSON envelope's ``c`` field.
USER_AGENT = "AnkiPaper/0.1"

#: Magic bytes that prefix every zstd frame (RFC 8478).
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

#: Hard cap on decompressed response bodies (AnkiWeb is well under this).
_DECOMPRESS_LIMIT_BYTES = 512 * 1024 * 1024

#: HTTP timeout for sync requests (AnkiWeb occasionally takes minutes
#: during incremental sync).
_REQUEST_TIMEOUT_SECONDS = 120

#: Truncated body preview included in error logs (chars after decoding).
_ERROR_BODY_PREVIEW_BYTES = 500


class SyncHttpError(RuntimeError):
    """Failure of an HTTP request to an AnkiWeb sync endpoint."""


def compress(data: bytes) -> bytes:
    """Compresses ``data`` with zstd (format compatible with the server)."""

    return zstd.ZstdCompressor().compress(data)


def decompress(data: bytes) -> bytes:
    """Decompresses a zstd payload from the AnkiWeb server."""

    return zstd.ZstdDecompressor().decompress(data, max_output_size=_DECOMPRESS_LIMIT_BYTES)


def decompress_if_zstd(data: bytes) -> bytes:
    """Returns ``data`` decompressed if it carries a zstd magic header.

    Used by ``downloadFiles`` — the server returns a zstd-compressed
    zip body, but ``decode_response`` would discard the decompressed
    bytes (it returns the original raw payload when JSON parsing fails).
    """

    if data[:4] == ZSTD_MAGIC:
        return decompress(data)
    return data


def make_session_key() -> str:
    """Generates a pseudo-random session_key (format like AnkiDroid).

    See ``_anki_repo/rslib/src/sync/http_client/mod.rs:109-113``: 16
    chars from an alphanumeric alphabet.
    """

    alphabet = string.ascii_letters + string.digits
    return "".join(random.choice(alphabet) for _ in range(16))


def post_json(
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
        Raw (zstd-compressed) response body. Use :func:`decode_response` to
        decompress and check for errors.

    Raises:
        SyncHttpError: on a network error or HTTP 4xx/5xx.
    """

    # Media sync lives under ``/msync/*`` (see
    # ``_anki_repo/rslib/src/sync/http_server/mod.rs:248-249``),
    # the collection lives under ``/sync/*``.
    url = urllib.parse.urljoin(endpoint, f"msync/{method}")
    body = compress(json.dumps(payload).encode("utf-8"))
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/octet-stream",
            SYNC_HEADER_NAME: json.dumps(
                {
                    "v": SYNC_VERSION,
                    "k": host_key,
                    "c": USER_AGENT,
                    "s": session_key,
                }
            ),
            "User-Agent": USER_AGENT,
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise SyncHttpError(
            f"AnkiWeb returned {exc.code} for {method}: "
            f"{_preview_body(exc)} or {exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise SyncHttpError(f"Network error during {method}: {exc.reason}") from exc


def _preview_body(exc: urllib.error.HTTPError) -> str:
    """Returns a short UTF-8 preview of an HTTP error body for logging."""

    body_bytes = exc.read()[:_ERROR_BODY_PREVIEW_BYTES]
    try:
        return body_bytes.decode("utf-8", errors="replace")
    except Exception:
        return repr(body_bytes)


def decode_response(raw: bytes) -> dict | list | bytes:
    """Decodes a zstd response and checks the ``JsonResult`` wrapper.

    Media-sync (``/msync/*``) wraps JSON responses in a ``JsonResult`` —
    an untagged enum: ``{"data": <T>, "err": ""}`` (Ok) or
    ``{"err": "..."}`` (Err). ``downloadFiles`` returns a raw zip.

    Args:
        raw: zstd-compressed response body.

    Returns:
        Decoded JSON object (for media sync) or raw bytes (for
        ``downloadFiles``).
    """

    try:
        decompressed = decompress(raw)
    except zstd.ZstdError:
        # Not zstd — maybe already decompressed, or not zstd at all.
        return raw

    try:
        wrapper = json.loads(decompressed)
    except json.JSONDecodeError:
        # Not JSON — this is a zip (downloadFiles) or another binary.
        return raw

    # ``JsonResult``:
    #   - Ok:   ``{"data": <T>, "err": ""}``
    #   - Err:  ``{"err": "..."}``
    if isinstance(wrapper, dict):
        err = wrapper.get("err")
        if isinstance(err, str) and err:
            raise SyncHttpError(f"AnkiWeb sync error: {err}")
        if "data" in wrapper:
            return wrapper["data"]
    return wrapper