"""Custom Prometheus collectors for AnkiPaper-specific gauges. Run on every scrape."""

from __future__ import annotations

import logging
import time
from functools import lru_cache

import psutil
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

from app.storage import get_account_store

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

        accounts = store.list_loaded_accounts()
        metric_collections_open = GaugeMetricFamily(
            "ankipaper_collections_open",
            "Number of accounts with a currently open collection.",
        )
        open_count = sum(1 for acc in accounts if acc.is_open())
        metric_collections_open.add_metric([], open_count)
        yield metric_collections_open

        metric_data_folder_size = GaugeMetricFamily(
            "ankipaper_data_folder_size",
            "Total size of the data folder for all accounts.",
        )
        data_folder_size = self._get_cached_data_folder_size(round(time.time() / 3600))
        metric_data_folder_size.add_metric([], data_folder_size)
        yield metric_data_folder_size

        metric_ram_usage = GaugeMetricFamily(
            "ankipaper_ram_usage",
            "Host RAM utilization percent.",
        )
        ram_usage = psutil.virtual_memory().percent
        metric_ram_usage.add_metric([], ram_usage)
        yield metric_ram_usage

    @staticmethod
    @lru_cache(maxsize=1) 
    def _get_cached_data_folder_size(ttl: int):
        del ttl
        store = get_account_store()
        return store.total_data_bytes()
