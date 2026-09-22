"""Collapsed, server-rendered rows for the deck picker."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.scheduler import DeckStats


@dataclass(slots=True)
class DeckRow:
    stats: DeckStats
    label: str
    depth: int
    is_expanded: bool
    toggle_query: str | None


def build_deck_rows(decks: list[DeckStats], expanded: str = "") -> list[DeckRow]:
    """Hide descendants unless every ancestor is explicitly expanded.

    Expansion is local to the page URL; it never modifies the Anki collection.
    Keep the backend's parent counts, which already include its child decks.
    """
    by_name = {deck.name: deck for deck in decks}
    parent_names = {deck.name.rpartition("::")[0] for deck in decks}
    expandable = {str(deck.deck_id) for deck in decks if deck.name in parent_names}
    open_ids = set(expanded.split(",")) & expandable
    rows = []
    for deck in decks:
        parts = deck.name.split("::")
        ancestors = [by_name.get("::".join(parts[:i])) for i in range(1, len(parts))]
        if any(parent is not None and str(parent.deck_id) not in open_ids for parent in ancestors):
            continue
        deck_id = str(deck.deck_id)
        is_expanded = deck_id in open_ids
        toggle_query = None
        if deck_id in expandable:
            toggle_query = ",".join(sorted(open_ids ^ {deck_id}))
        rows.append(DeckRow(deck, parts[-1], len(parts) - 1, is_expanded, toggle_query))
    return rows
