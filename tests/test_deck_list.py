"""Deck picker regression tests: python -m unittest discover -s tests -v."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from starlette.requests import Request

from app.domain.scheduler import DeckStats
from app.main import create_app
from app.web.decks import build_deck_rows
from app.web.routes.study import home


def sample_decks():
    return [
        DeckStats(1, "Genkikani", 12, 3, 20),
        DeckStats(2, "Genkikani::Lesson 06", 5, 1, 8),
        DeckStats(3, "Genkikani::Lesson 06::1 Kanjis", 2, 1, 4),
        DeckStats(4, "Genkikani::Lesson 07", 7, 2, 12),
        DeckStats(5, "Other", 0, 0, 0),
        DeckStats(6, "Other::Child", 0, 0, 0),
        DeckStats(7, "Standalone", 1, 0, 0),
    ]


class DeckRowsTests(unittest.TestCase):
    def test_default_shows_only_roots_and_keeps_parent_counts(self):
        rows = build_deck_rows(sample_decks())
        self.assertEqual([row.stats.deck_id for row in rows], [1, 5, 7])
        self.assertEqual((rows[0].stats.new, rows[0].stats.learning, rows[0].stats.review), (12, 3, 20))
        self.assertEqual(rows[0].toggle_query, "1")
        self.assertIsNone(rows[-1].toggle_query)

    def test_expanding_parent_shows_only_immediate_children(self):
        rows = build_deck_rows(sample_decks(), "1")
        self.assertEqual([row.stats.deck_id for row in rows], [1, 2, 4, 5, 7])
        self.assertEqual(rows[1].label, "Lesson 06")
        self.assertEqual(rows[1].depth, 1)
        self.assertEqual(rows[1].toggle_query, "1,2")
        self.assertEqual(rows[0].toggle_query, "")

    def test_nested_expansion_and_collapse_preserve_other_groups(self):
        rows = build_deck_rows(sample_decks(), "1,2,5")
        self.assertEqual([row.stats.deck_id for row in rows], [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual((rows[2].label, rows[2].depth), ("1 Kanjis", 2))
        collapsed = build_deck_rows(sample_decks(), rows[0].toggle_query)
        self.assertEqual([row.stats.deck_id for row in collapsed], [1, 5, 6, 7])

    def test_invalid_and_leaf_ids_are_ignored(self):
        rows = build_deck_rows(sample_decks(), "junk,999,3,1,1")
        self.assertEqual([row.stats.deck_id for row in rows], [1, 2, 4, 5, 7])
        self.assertEqual(rows[0].toggle_query, "")
        self.assertEqual(build_deck_rows([]), [])


class DeckPageTests(unittest.IsolatedAsyncioTestCase):
    async def render_home(self, decks, expanded=""):
        app = create_app()
        app.state.templates.env.globals["csrf_token"] = lambda request: "test-token"
        request = Request({
            "type": "http", "method": "GET", "path": "/", "root_path": "",
            "query_string": b"", "headers": [(b"host", b"localhost")],
            "scheme": "http", "server": ("localhost", 80), "app": app,
        })
        account = SimpleNamespace(
            username="test",
            manager=SimpleNamespace(has_collection=lambda: True, run=AsyncMock(return_value=decks)),
            sync_state=SimpleNamespace(media_collection_too_large=False),
        )
        with patch("app.web.routes.study._is_sync_required", new=AsyncMock(return_value=False)):
            response = await home(request, expanded=expanded, account=account)
        self.assertEqual(response.status_code, 200)
        return response.body.decode()

    async def test_default_and_expanded_pages_have_working_study_links(self):
        html = await self.render_home(sample_decks())
        self.assertIn('href="/deck/1/study"', html)
        self.assertNotIn('href="/deck/2/study"', html)
        self.assertIn('href="/?expanded=1#deck-1"', html)
        self.assertIn('aria-expanded="false"', html)
        html = await self.render_home(sample_decks(), "1,2")
        self.assertIn('href="/deck/3/study"', html)
        self.assertIn('>1 Kanjis</a>', html)
        self.assertIn('aria-label="Collapse Genkikani"', html)
        self.assertNotIn('href="/deck/6/study"', html)

    async def test_deck_names_are_escaped(self):
        html = await self.render_home([DeckStats(1, '<script>alert("x")</script>', 0, 0, 0)])
        self.assertNotIn('<script>alert', html)
        self.assertIn('&lt;script&gt;', html)


if __name__ == "__main__":
    unittest.main()
