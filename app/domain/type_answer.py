"""Support for Anki's "type in the answer" cards (``{{type:Field}}``)."""

from __future__ import annotations

import re
from dataclasses import dataclass

import anki.collection

# Anki's placeholder for the ``type`` template filter.
_TYPE_PLACEHOLDER = re.compile(r"\[\[type:(?P<tag>[^\]]+)\]\]")

# Input box rendered on the question side (AnkiDroid's ``useInputTag`` mode).
_TYPE_INPUT_HTML = (
  '<input type="text" id="typeans" name="typed_answer" class="type-answer"'
  ' autocomplete="off" autocapitalize="off" autocorrect="off"'
  ' spellcheck="false" placeholder="Type your answer">'
)

# Shown when the template points to a field the note does not have.
_MISSING_FIELD_HTML = '<span class="muted">(type-in field not found)</span>'


@dataclass(slots=True)
class _TypeTag:
  """Parsed ``type:`` filter tag: ``Field``, ``cloze:Field`` or ``nc:Field``."""

  field_name: str
  """Name of the note field the answer is typed against."""

  combining: bool = True
  """Whether combining marks are normalized (``nc:`` switches it off)."""

  is_cloze: bool = False
  """Whether the expected answer is the cloze text of the card's ordinal."""


def resolve_type_placeholders(
  col: anki.collection.Collection,
  fields: dict[str, str],
  question_html: str,
  answer_html: str,
  typed_answer: str = "",
  cloze_ordinal: int = 0,
) -> tuple[str, str]:
    """Replace Anki's ``[[type:Field]]`` placeholders in the rendered card.

    The question side gets an input box for the typed answer, the answer
    side gets the typed answer compared with the correct one (Anki's own
    ``compare_answer``, so the output matches the desktop client).

    Args:
        col: open collection (used for the backend comparison helpers).
        fields: raw HTML of the note's fields, keyed by field name.
        question_html: rendered question side of the card.
        answer_html: rendered answer side of the card.
        typed_answer: what the user typed on the question side.
        cloze_ordinal: 1-based ordinal of the cloze the card shows
            (0 — the card is not a cloze card).

    Returns:
        The (question_html, answer_html) pair with the placeholders
        resolved.
    """

    match = _TYPE_PLACEHOLDER.search(question_html) or _TYPE_PLACEHOLDER.search(answer_html)
    if match is None:
      return question_html, answer_html

    tag = _parse_type_tag(match.group("tag"))
    field_html = fields.get(tag.field_name)
    if field_html is None:
      return (
        _TYPE_PLACEHOLDER.sub(lambda _m: _MISSING_FIELD_HTML, question_html),
        _TYPE_PLACEHOLDER.sub(lambda _m: _MISSING_FIELD_HTML, answer_html),
      )

    expected = field_html
    if tag.is_cloze and cloze_ordinal > 0:
      expected = col.extract_cloze_for_typing(field_html, cloze_ordinal)

    if not expected:
      return (
        _TYPE_PLACEHOLDER.sub("", question_html),
        _TYPE_PLACEHOLDER.sub("", answer_html),
      )

    comparison = col.compare_answer(expected, typed_answer, tag.combining)

    first = True

    def _question_replacement(_match: re.Match[str]) -> str:
      """Render the input box once, drop the rest of the placeholders."""

      nonlocal first
      if first:
        first = False
        return _TYPE_INPUT_HTML
      return ""

    return (
        _TYPE_PLACEHOLDER.sub(_question_replacement, question_html),
        _TYPE_PLACEHOLDER.sub(lambda _m: comparison, answer_html),
    )


def _parse_type_tag(tag: str) -> _TypeTag:
  """Parse a ``type:`` filter tag into its parts.

  Args:
    tag: tag text as written in the template, e.g. ``Back``,
      ``cloze:Text`` or ``nc:Text``.

  Returns:
    The parsed tag.
  """

  name = tag.strip()
  combining = True
  is_cloze = False

  if ":" in name:
    prefix, rest = name.split(":", 1)
    prefix = prefix.strip().lower()
    if prefix in ("cloze", "nc"):
      name = rest.strip()
      is_cloze = prefix == "cloze"
      combining = prefix != "nc"

  return _TypeTag(field_name=name, combining=combining, is_cloze=is_cloze)