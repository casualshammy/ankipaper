"""Custom Prometheus collectors for AnkiPaper-specific gauges. Run on every scrape."""

from __future__ import annotations

import logging

from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

from app.storage.account import get_account_store

logger = logging.getLogger(__name__)


class AnkiPaperCollector(Collector):
    """Emits account/collection gauges on every scrape."""

    def collect(self):
        try:
            store = get_account_store()
        except Exception:
            logger.exception("AnkiPaperCollector: store unavailable")
            return

        managers = store.iter_managers()
        accounts_total = GaugeMetricFamily(
            "ankipaper_accounts_total",
            "Total number of registered AnkiWeb accounts.",
        )
        accounts_total.add_metric([], len(managers))
        yield accounts_total

        collections_open = GaugeMetricFamily(
            "ankipaper_collections_open",
            "Number of accounts with a currently open collection.",
        )
        open_count = sum(1 for m in managers if m.is_open())
        collections_open.add_metric([], open_count)
        yield collections_open
