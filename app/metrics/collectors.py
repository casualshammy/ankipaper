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
        
        metric_accounts_total = GaugeMetricFamily(
            "ankipaper_accounts_total",
            "Total number of registered AnkiWeb accounts.",
        )
        metric_accounts_total.add_metric([], store.total_accounts_on_disk())
        yield metric_accounts_total

        managers = store.iter_managers()
        metric_collections_open = GaugeMetricFamily(
            "ankipaper_collections_open",
            "Number of accounts with a currently open collection.",
        )
        open_count = sum(1 for m in managers if m.is_open())
        metric_collections_open.add_metric([], open_count)
        yield metric_collections_open

        metric_data_folder_size = GaugeMetricFamily(
            "ankipaper_data_folder_size",
            "Total size of the data folder for all accounts.",
        )
        data_folder_size = store.total_data_bytes()
        metric_data_folder_size.add_metric([], data_folder_size)
        yield metric_data_folder_size
