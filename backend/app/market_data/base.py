from abc import ABC, abstractmethod


class MarketDataSource(ABC):
    """
    Abstract interface for market data providers.

    Implementations must:
    1. Generate/fetch price ticks and write them to the shared PriceCache.
    2. Run as a long-lived background task via start().
    3. Support dynamic ticker addition/removal.
    """

    @abstractmethod
    async def start(self, cache: "PriceCache") -> None:
        """
        Begin producing price data. Runs indefinitely as a background task.
        Writes PriceTick objects into the provided PriceCache.

        Args:
            cache: The shared PriceCache to write updates into.
        """
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully shut down the data source."""
        ...

    @abstractmethod
    def add_ticker(self, ticker: str) -> None:
        """
        Start tracking a new ticker.

        For the simulator: seeds at a realistic price and begins generating.
        For Massive: adds to the next poll batch.
        """
        ...

    @abstractmethod
    def remove_ticker(self, ticker: str) -> None:
        """
        Stop tracking a ticker.

        The source stops generating/polling for this ticker.
        The PriceCache entry is also cleared.
        """
        ...

    @abstractmethod
    def get_tracked_tickers(self) -> list[str]:
        """Return the list of currently tracked tickers."""
        ...
