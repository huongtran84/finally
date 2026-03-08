import logging
import os

from .base import MarketDataSource
from .massive import MassiveSource
from .simulator import SimulatorSource

logger = logging.getLogger(__name__)


def create_market_data_source() -> MarketDataSource:
    """
    Factory function that returns the appropriate market data source
    based on environment configuration.

    - If MASSIVE_API_KEY is set and non-empty → MassiveSource
    - Otherwise → SimulatorSource (default)
    """
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()

    if api_key:
        logger.info(
            "Using Massive API for market data (poll interval: %s)",
            os.environ.get("MASSIVE_POLL_INTERVAL", "15"),
        )
        return MassiveSource(api_key=api_key)
    else:
        logger.info("Using market simulator for market data")
        return SimulatorSource()
