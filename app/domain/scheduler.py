"""Logic on top of the Anki collection: deck statistics, card reviews, answers.

All requests to the local collection go through the backend API
(``col._backend.get_queued_cards``, ``backend.answer_card`` and similar) —
not through direct SQL.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from enum import IntEnum

import anki.collection
import anki.errors
import anki.scheduler_pb2 as sp

logger = logging.getLogger(__name__)


class Rating(IntEnum):
    """Anki's 4-button answer scale."""

    AGAIN = 1
    HARD = 2
    GOOD = 3
    EASY = 4

    @property
    def label(self) -> str:
        return _RATING_LABELS[self]


_RATING_LABELS = {
    Rating.AGAIN: "Again",
    Rating.HARD: "Hard",
    Rating.GOOD: "Good",
    Rating.EASY: "Easy",
}


# Anki user flags: 0 = none, 1 = red, 2 = orange, 3 = green, 4 = blue.
_FLAG_LABELS: dict[int, str] = {
    0: "none",
    1: "red",
    2: "yellow",
    3: "green",
    4: "blue",
}


@dataclass(slots=True)
class DeckStats:
    """Statistics for a single deck."""

    deck_id: int
    name: str
    new: int
    learning: int
    review: int
    is_filtered: bool = False


@dataclass(slots=True)
class DueBreakdown:
    """Breakdown of due cards for the current session."""

    new: int
    learning: int
    review: int

    @property
    def total(self) -> int:
        return self.new + self.learning + self.review


@dataclass(slots=True)
class CardIntervals:
    """Interval preview for the rating buttons."""

    again: str
    hard: str
    good: str
    easy: str


@dataclass(slots=True)
class CardView:
    """Card view prepared for review."""

    card_id: int
    question_html: str
    answer_html: str
    is_cloze: bool
    note_type_name: str
    fields: list[tuple[str, str]]
    card_type: str = "new"
    """Card type: ``new``, ``learning``, ``review`` or ``relearning``."""
    intervals: CardIntervals | None = None
    """Interval preview for the Again/Hard/Good/Easy buttons (or ``None``)."""
    flag: int = 0
    """User flag 0..4 (0 = none, 1 = red, 2 = orange, 3 = green, 4 = blue)."""

    is_marked: bool = False
    """True if the card's note carries Anki's ``marked`` tag (the "star")."""

    @property
    def css_class(self) -> str:
        """CSS class for the current card type (for highlighting in the template)."""

        return {
            "new": "tag-current-new",
            "learning": "tag-current-learning",
            "review": "tag-current-review",
            "relearning": "tag-current-learning",
        }.get(self.card_type, "tag-current-new")

    @property
    def flag_label(self) -> str:
        """Short e-ink-friendly label for the current flag."""

        return _FLAG_LABELS.get(self.flag, "none")


@dataclass(slots=True)
class NextInterval:
    """Time after which the card is due again."""

    seconds: int
    label: str


@dataclass(slots=True)
class AnswerOutcome:
    """Result of applying an answer."""

    next_card_id: int | None
    next_interval: NextInterval | None


def _queued_card_for(
    col: anki.collection.Collection,
    deck_id: int,
) -> sp.QueuedCards | None:
    """Returns queued cards for the deck, or None if the deck is not selected."""

    col.decks.select(int(deck_id))
    return col._backend.get_queued_cards(  # type: ignore[attr-defined]
        fetch_limit=1,
        intraday_learning_only=False,
    )


def list_deck_stats(col: anki.collection.Collection) -> list[DeckStats]:
    """Returns statistics for all decks (new / learning / review).

    Uses the backend deck tree so reading the home page does not change
    ``curDeck``. Selecting every deck before calling ``get_queued_cards``
    marked the collection as modified and caused ``sync_status`` to report
    pending changes immediately after a successful sync.
    """

    tree = col._backend.deck_tree(now=int(time.time()))  # type: ignore[attr-defined]
    result: list[DeckStats] = []

    def _append_nodes(nodes: list, parent_name: str = "") -> None:
        for node in nodes:
            name = f"{parent_name}::{node.name}" if parent_name else str(node.name)
            result.append(
                DeckStats(
                    deck_id=int(node.deck_id),
                    name=name,
                    new=int(node.new_count),
                    learning=int(node.learn_count),
                    review=int(node.review_count),
                    is_filtered=bool(node.filtered),
                )
            )
            _append_nodes(node.children, name)

    _append_nodes(tree.children)
    return result


def get_deck_due_count(
    col: anki.collection.Collection,
    deck_id: int,
) -> int:
    """How many due cards are in the deck in total (new + learning + review)."""

    return get_deck_due_breakdown(col, deck_id).total


def get_deck_due_breakdown(
    col: anki.collection.Collection,
    deck_id: int,
) -> "DueBreakdown":
    """Breakdown of due cards by new/learning/review for the current deck.

    Uses ``col._backend.get_queued_cards``, which returns ready-made counts
    without manual SQL.
    """

    queued = _queued_card_for(col, deck_id)
    if queued is None:
        return DueBreakdown(new=0, learning=0, review=0)
    return DueBreakdown(
        new=int(queued.new_count),
        learning=int(queued.learning_count),
        review=int(queued.review_count),
    )


def rebuild_filtered_deck(
    col: anki.collection.Collection,
    deck_id: int,
) -> int:
    """Rebuilds a filtered deck using its search terms.

    Args:
        col: open collection.
        deck_id: id of the filtered deck.

    Returns:
        Number of cards that ended up in the deck after the rebuild.
    """

    result = col._backend.rebuild_filtered_deck(int(deck_id))  # type: ignore[attr-defined]
    return int(result.count)


def set_card_flag(
    col: anki.collection.Collection,
    card_id: int,
    flag: int,
) -> int:
    """Sets the user flag on a card (0..4).

    Args:
        col: open collection.
        card_id: target card id.
        flag: 0 = no flag, 1 = red, 2 = orange, 3 = green, 4 = blue.

    Returns:
        Number of cards whose flag was actually changed (0 or 1).

    Raises:
        ValueError: if ``flag`` is outside 0..4 or the card does not exist.
    """

    if not 0 <= flag <= 4:
        raise ValueError(f"invalid flag: {flag!r}")
    try:
        col.get_card(card_id)
    except anki.errors.NotFoundError as e:
        raise ValueError(f"card not found: {card_id}") from e
    result = col.set_user_flag_for_cards(flag, [card_id])
    return int(result.count)


def set_card_marked(
    col: anki.collection.Collection,
    card_id: int,
    marked: bool,
) -> bool:
    """Toggles Anki's ``marked`` (star) tag on the card's note.

    Args:
        col: open collection.
        card_id: target card id.
        marked: ``True`` to add the ``marked`` tag, ``False`` to remove it.

    Returns:
        The resulting marked state of the card.

    Raises:
        ValueError: if the card does not exist.
    """

    try:
        card = col.get_card(card_id)
    except anki.errors.NotFoundError as e:
        raise ValueError(f"card not found: {card_id}") from e
    note = card.note()
    if marked and not note.has_tag("marked"):
        note.add_tag("marked")
        note.flush()
    elif not marked and note.has_tag("marked"):
        note.remove_tag("marked")
        note.flush()
    return bool(note.has_tag("marked"))


def get_next_card(
    col: anki.collection.Collection,
    deck_id: int,
) -> CardView | None:
    """Returns the next due card in the deck, or None."""

    queued = _queued_card_for(col, deck_id)
    if queued is None or not queued.cards:
        logger.info(
            "get_next_card: deck_id=%s no due cards (new=%s learning=%s review=%s)",
            deck_id,
            None if queued is None else queued.new_count,
            None if queued is None else queued.learning_count,
            None if queued is None else queued.review_count,
        )
        return None
    card_id = int(queued.cards[0].card.id)
    card_state = queued.cards[0].states.current
    card_type = _card_type_from_state(card_state)
    intervals = _compute_intervals(queued.cards[0])
    logger.info(
        "get_next_card: deck_id=%s next card_id=%s type=%s "
        "state(normal=%s filtered=%s) intervals(again=%s hard=%s good=%s easy=%s) "
        "remaining new=%s learning=%s review=%s",
        deck_id,
        card_id,
        card_type,
        card_state.HasField("normal"),
        card_state.HasField("filtered"),
        intervals.again,
        intervals.hard,
        intervals.good,
        intervals.easy,
        queued.new_count,
        queued.learning_count,
        queued.review_count,
    )
    return _load_card_view(col, card_id, card_type, intervals)


def get_card_view(
    col: anki.collection.Collection,
    card_id: int,
    card_type: str = "new",
    intervals: CardIntervals | None = None,
) -> CardView | None:
    """Returns the render view of a specific card, or None.

    Args:
        col: open collection.
        card_id: card id.
        card_type: card type (``new``/``learning``/``review``/``relearning``).
            Determined on the front side and passed through the form so
            that this information is not lost on the back page (where a
            repeated ``get_queued_cards`` will no longer return this card).
        intervals: interval preview; passed through the form from the
            front page.
    """

    if not col.get_card(card_id):
        return None
    return _load_card_view(col, card_id, card_type, intervals)


def answer_card(
    col: anki.collection.Collection,
    card_id: int,
    rating: Rating,
    new_state: sp.SchedulingState | None = None,
    current_state: sp.SchedulingState | None = None,
    deck_id: int | None = None,
) -> AnswerOutcome:
    """Applies the user's answer and returns the next step.

    States are taken from ``get_queued_cards`` for the current deck — this
    guarantees that the states passed to ``answer_card`` correspond to the
    card's position in the due queue. Without this, Anki may interpret the
    transition incorrectly (for example, after ``Good`` the card goes to
    ``review`` with ``due`` tomorrow, and the ``new``/``review`` count in
    the current deck breaks because of that).

    Args:
        col: open collection.
        card_id: card id.
        rating: chosen rating (1..4).
        new_state: ready ``new_state`` (if None — taken from
            ``get_queued_cards``).
        current_state: ready ``current_state``.
        deck_id: id of the deck from which the queue is taken. If None —
            the deck the card currently lives in is used.
    """

    backend = col._backend  # type: ignore[attr-defined]

    if new_state is None or current_state is None:
        if deck_id is None:
            card = col.get_card(card_id)
            deck_id = int(card.did)
        queued = _queued_card_for(col, int(deck_id))
        head = queued.cards[0] if queued and queued.cards else None
        if head is not None and int(head.card.id) == int(card_id):
            current_state = head.states.current
            new_state = {
                Rating.AGAIN: head.states.again,
                Rating.HARD: head.states.hard,
                Rating.GOOD: head.states.good,
                Rating.EASY: head.states.easy,
            }[rating]
            logger.info(
                "answer_card: card_id=%s deck_id=%s using queued.states rating=%s",
                card_id,
                deck_id,
                int(rating),
            )
        else:
            # Card is not in the due queue (e.g. it's not due, or the
            # answer came from a different deck). Fallback to
            # get_scheduling_states.
            logger.warning(
                "answer_card: card_id=%s NOT in deck_id=%s queue (head=%s); using get_scheduling_states",
                card_id,
                deck_id,
                int(head.card.id) if head else None,
            )
            states = backend.get_scheduling_states(int(card_id))
            current_state = states.current
            new_state = {
                Rating.AGAIN: states.again,
                Rating.HARD: states.hard,
                Rating.GOOD: states.good,
                Rating.EASY: states.easy,
            }[rating]

    backend.answer_card(
        sp.CardAnswer(
            card_id=int(card_id),
            current_state=current_state,
            new_state=new_state,
            rating=int(rating),
            answered_at_millis=int(time.time() * 1000),
            milliseconds_taken=0,
        )
    )

    # IMPORTANT: when looking for the next card we use the ORIGINAL deck
    # (the one passed by the caller), not ``card.did``. ``card.did`` is the
    # leaf deck of the card, and in Anki a parent deck includes child
    # decks, but not vice versa: if ``deck_id`` is the parent and the card
    # lives in a child, then switching to ``card.did`` after the answer
    # narrows the queue to the child only — there may be no due cards
    # there even though there are some in the parent.
    next_deck_id = int(deck_id) if deck_id is not None else int(col.get_card(card_id).did)
    queued = _queued_card_for(col, next_deck_id)
    next_cid: int | None = None
    if queued is not None and queued.cards:
        next_cid = int(queued.cards[0].card.id)

    logger.info(
        "answer_card: card_id=%s rating=%s deck_id=%s remaining new=%s learning=%s review=%s next=%s",
        card_id,
        int(rating),
        next_deck_id,
        queued.new_count if queued else 0,
        queued.learning_count if queued else 0,
        queued.review_count if queued else 0,
        next_cid,
    )

    return AnswerOutcome(
        next_card_id=next_cid,
        next_interval=_interval_from_state(new_state),
    )


def _load_card_view(
    col: anki.collection.Collection,
    card_id: int,
    card_type: str = "new",
    intervals: CardIntervals | None = None,
) -> CardView:
    """Builds a CardView for a card by id.

    Args:
        col: open collection.
        card_id: card id.
        card_type: type (``new`` / ``learning`` / ``review`` / ``relearning``),
            determined from ``queued.cards[0].states.current``. ``"new"`` is
            used as a fallback if no type was passed.
        intervals: interval preview (see ``_compute_intervals``). If
            ``None`` — the ``intervals`` field will be ``None``, and the
            template will show ``"—"`` on the back page.
    """

    card = col.get_card(card_id)
    note = card.note()
    notetype = card.note_type()

    is_cloze = bool(notetype.get("type") == anki.collection.MODEL_CLOZE)

    fields: list[tuple[str, str]] = []
    for fld, fval in zip(notetype.get("flds", []), list(note.fields)):
        fname = fld["name"] if isinstance(fld, dict) else str(fld)
        fields.append((fname, _strip_html(fval)))

    return CardView(
        card_id=int(card_id),
        question_html=_sanitize_for_eink(card.question()),
        answer_html=_sanitize_for_eink(card.answer()),
        is_cloze=is_cloze,
        note_type_name=str(notetype.get("name", "")),
        fields=fields,
        card_type=card_type,
        intervals=intervals,
        flag=int(card.user_flag()),
        is_marked=bool(card.note().has_tag("marked")),
    )


def _normal_to_card_type(normal: sp.Normal) -> str:
    """Converts ``SchedulingState.Normal`` to a user-facing type.

    ``Normal`` is a nested ``oneof`` variant of ``SchedulingState`` and
    at the same time a separate field ``ReschedulingFilter.original_state``.
    It has no ``normal`` field (unlike ``SchedulingState``), so we unpack
    it directly.
    """

    if normal.HasField("new"):
        return "new"
    if normal.HasField("review"):
        return "review"
    if normal.HasField("relearning"):
        return "relearning"
    if normal.HasField("learning"):
        return "learning"
    return "new"


def _card_type_from_state(state: sp.SchedulingState) -> str:
    """Determines the user-facing card type (``SchedulingState.current``).

    Handles three variants of ``SchedulingState``:
    - ``normal`` — a regular deck; subtype by ``normal.kind`` (new /
      learning / review / relearning);
    - ``filtered.rescheduling`` — a filtered deck in rescheduling mode;
      ``original_state`` has type ``Normal`` (see
      ``scheduler.proto:114-115``); we read it via ``_normal_to_card_type``;
    - ``filtered.preview`` — preview mode; treated as ``"new"``.
    """

    if state.HasField("normal"):
        return _normal_to_card_type(state.normal)

    if state.HasField("filtered"):
        filtered = state.filtered
        # Rescheduling: read the original state recursively.
        if filtered.HasField("rescheduling"):
            return _normal_to_card_type(filtered.rescheduling.original_state)
        # Preview (haven't started learning yet) — show as ``new`` for the UI.
        return "new"

    return "new"


def _interval_from_state(state: sp.SchedulingState) -> NextInterval | None:
    """Converts a SchedulingState into a human-readable interval.

    ``Relearning`` in the current version of the proto contains nested
    ``review`` (days) and ``learning`` (seconds); see
    ``_anki_repo/proto/anki/scheduler.proto:98-101``.
    """

    if state.HasField("normal"):
        normal = state.normal
        if normal.HasField("review"):
            days = int(normal.review.scheduled_days)
            return NextInterval(
                seconds=days * 86400,
                label=_format_interval(days * 86400),
            )
        if normal.HasField("learning"):
            secs = int(normal.learning.scheduled_secs or 600)
            return NextInterval(seconds=secs, label=_format_interval(secs))
        if normal.HasField("relearning"):
            rel = normal.relearning
            if rel.HasField("review"):
                days = int(rel.review.scheduled_days)
                return NextInterval(
                    seconds=days * 86400,
                    label=_format_interval(days * 86400),
                )
            if rel.HasField("learning"):
                secs = int(rel.learning.scheduled_secs or 600)
                return NextInterval(seconds=secs, label=_format_interval(secs))
        if normal.HasField("new"):
            return NextInterval(seconds=0, label="new")
    return None


def _label_or_dash(state: sp.SchedulingState) -> str:
    """Short interval label, or ``"—"`` if the state is not recognised."""

    interval = _interval_from_state(state)
    return interval.label if interval is not None else "—"


def _compute_intervals(queued_card) -> CardIntervals:
    """Interval preview for the Again/Hard/Good/Easy buttons.

    From ``queued_card.states`` (type ``SchedulingStates``) we take the
    ``again``/``hard``/``good``/``easy`` variants — each is a
    ``SchedulingState`` from which ``_interval_from_state`` extracts the
    interval.

    For filtered decks (``rescheduling``) the states can be ``Filtered`` —
    ``_interval_from_state`` correctly returns ``"—"`` for them, and the
    preview shows dashes — which is correct: in cram mode the actual
    interval depends on the user's choice at the moment of the answer.
    """

    states = queued_card.states
    return CardIntervals(
        again=_label_or_dash(states.again),
        hard=_label_or_dash(states.hard),
        good=_label_or_dash(states.good),
        easy=_label_or_dash(states.easy),
    )


def _format_interval(seconds: int) -> str:
    """Renders seconds in a short e-ink-friendly format.

    Months are shown as ``X.Y mo`` with a single decimal place
    (for example, ``4.3 mo``), so that fractional values like
    4½ months (``4.5 mo``) are visible.
    """

    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    days = seconds // 86400
    if days < 30:
        return f"{days}d"
    if days < 365:
        months = days / 30
        return f"{months:.1f} mo"
    return f"{days // 365}y"


def _strip_html(text: str) -> str:
    """Minimal HTML stripper for plain rendering on Kindle."""

    cleaned = re.sub(r"<[^>]+>", "", text)
    return cleaned.replace("&nbsp;", " ").strip()


def _sanitize_for_eink(html: str) -> str:
    """Prepares card HTML for rendering on e-ink Kindle.

    - Removes ``<script>`` (Kindle WebKit 1.x does not support JS and it is
      a security hole).
    - Removes ``<audio>``, ``<video>``, ``<source>``, ``<iframe>``,
      ``<object>``, ``<embed>``, ``<canvas>`` — Kindle cannot play them.
    - Rewrites ``<img src="filename">`` → ``<img src="/ms/filename">``, so
      that images are loaded through our media route
      (``app/web/routes/media.py``). Anki stores files in
      ``collection.media/`` next to ``collection.anki21``.
    - Preserves ``<style>`` of the card template — without it the card
      loses Anki's original styling. The blue ``color: blue`` issue for
      cloze is handled by an ``!important`` override in ``eink.css``.
    """

    html = re.sub(
        r"<script\b[^>]*>.*?</script>",
        "",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # Remove the whole tag with contents (Anki renders relearn_audio/[sound:...]
    # as <audio>, video as <video>, etc.).
    for tag in ("audio", "video", "source", "iframe", "object", "embed", "canvas"):
        html = re.sub(
            rf"<{tag}\b[^>]*>.*?</{tag}>",
            "",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        # Self-closing variants: <tag ... />
        html = re.sub(
            rf"<{tag}\b[^>]*/?>",
            "",
            html,
            flags=re.IGNORECASE,
        )

    # <img src="filename"> → <img src="/ms/filename">. Only touch the src
    # attribute; keep the rest (alt, style, width, height) intact.
    def _rewrite_img(match: re.Match[str]) -> str:
        full = match.group(0)
        src = match.group("src")
        # Do not touch absolute URLs and data: URIs.
        if src.startswith(("http://", "https://", "data:", "/ms/")):
            return full
        return re.sub(
            r'src="[^"]*"',
            f'src="/ms/{src}"',
            full,
            count=1,
        )

    html = re.sub(
        r'<img\b[^>]*\bsrc="(?P<src>[^"]+)"[^>]*>',
        _rewrite_img,
        html,
        flags=re.IGNORECASE,
    )

    # <link href="filename.css"> → <link href="/ms/filename.css">. Anki
    # embeds notetype styles via <link rel="stylesheet">.
    def _rewrite_link(match: re.Match[str]) -> str:
        full = match.group(0)
        href = match.group("href")
        if href.startswith(("http://", "https://", "data:", "/ms/")):
            return full
        return re.sub(
            r'href="[^"]*"',
            f'href="/ms/{href}"',
            full,
            count=1,
        )

    html = re.sub(
        r'<link\b[^>]*\bhref="(?P<href>[^"]+)"[^>]*>',
        _rewrite_link,
        html,
        flags=re.IGNORECASE,
    )

    # Inside <style>: url(filename.ttf) → url(/ms/filename.ttf). Anki
    # embeds fonts via @font-face { src: url("_NotoSansJP.otf") }.
    def _rewrite_style_url(match: re.Match[str]) -> str:
        full = match.group(0)
        url = match.group("url")
        if url.startswith(("http://", "https://", "data:", "/ms/")):
            return full
        return re.sub(
            r"url\([^)]*\)",
            f"url(/ms/{url})",
            full,
            count=1,
        )

    def _rewrite_style_block(match: re.Match[str]) -> str:
        block = match.group(0)
        return re.sub(
            r"url\(\s*['\"]?(?P<url>[^'\")]+)['\"]?\s*\)",
            _rewrite_style_url,
            block,
            flags=re.IGNORECASE,
        )

    html = re.sub(
        r"<style\b[^>]*>.*?</style>",
        _rewrite_style_block,
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )

    return html