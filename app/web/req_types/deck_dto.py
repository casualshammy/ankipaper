"""Request/response payloads for the deck-tree endpoints."""

from __future__ import annotations

from pydantic import BaseModel


class DeckCollapseReq(BaseModel):
    """Body of a deck collapse / expand request."""

    collapsed: bool
