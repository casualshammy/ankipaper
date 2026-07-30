"""Shared sync-endpoint constants for ``app.sync.*`` modules.

The AnkiWeb sync service may redirect clients to a specific shard
(``sync20.ankiweb.net``, ``sync21.ankiweb.net``, ...) via
``new_endpoint`` in ``sync_status`` / ``sync_collection`` responses.
When no shard is specified we fall back to ``DEFAULT_ENDPOINT``.

All endpoints are stored as a single trailing-slashed URL; use
:func:`normalize_endpoint` to coerce a possibly-empty or non-normalised
value into that form before passing it to AnkiWeb.
"""

from __future__ import annotations

DEFAULT_ENDPOINT: str = "https://sync.ankiweb.net/"

def normalize_endpoint(value: str | None) -> str:
    """Returns ``value`` as a single trailing-slashed URL.

    ``None`` or empty strings fall back to :data:`DEFAULT_ENDPOINT`.
    Existing trailing slashes are preserved (one final slash only).
    """

    return (value or DEFAULT_ENDPOINT).rstrip("/") + "/"
