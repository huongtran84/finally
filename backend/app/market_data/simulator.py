import asyncio
import math
import random
from datetime import datetime, timezone

import numpy as np

from .base import MarketDataSource
from .cache import PriceCache
from .models import TickerConfig


# Default tickers with realistic seed prices and per-ticker volatility
DEFAULT_TICKERS: list[TickerConfig] = [
    TickerConfig(ticker="AAPL",  seed_price=192.0, volatility=0.25, sector="tech"),
    TickerConfig(ticker="GOOGL", seed_price=176.0, volatility=0.28, sector="tech"),
    TickerConfig(ticker="MSFT",  seed_price=420.0, volatility=0.22, sector="tech"),
    TickerConfig(ticker="AMZN",  seed_price=185.0, volatility=0.30, sector="tech"),
    TickerConfig(ticker="TSLA",  seed_price=245.0, volatility=0.55, sector="tech"),
    TickerConfig(ticker="NVDA",  seed_price=880.0, volatility=0.45, sector="tech"),
    TickerConfig(ticker="META",  seed_price=500.0, volatility=0.32, sector="tech"),
    TickerConfig(ticker="JPM",   seed_price=195.0, volatility=0.20, sector="finance"),
    TickerConfig(ticker="V",     seed_price=280.0, volatility=0.18, sector="finance"),
    TickerConfig(ticker="NFLX",  seed_price=620.0, volatility=0.35, sector="tech"),
]

# Sector correlation: same-sector tickers have 0.6 correlation, cross-sector 0.2
SECTOR_CORRELATION: dict[tuple[str, str], float] = {
    ("tech", "tech"): 0.6,
    ("finance", "finance"): 0.5,
    ("tech", "finance"): 0.2,
    ("finance", "tech"): 0.2,
}
DEFAULT_CROSS_CORRELATION = 0.1

# Simulation parameters
TICK_INTERVAL = 0.5  # seconds between price updates
TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600  # ~5.9M seconds
DT = TICK_INTERVAL / TRADING_SECONDS_PER_YEAR

# Random event parameters
EVENT_PROBABILITY = 0.003  # ~0.3% chance per tick per ticker (~1 event per ticker per 3 min)
EVENT_MIN_PCT = 0.02       # minimum event shock: 2%
EVENT_MAX_PCT = 0.05       # maximum event shock: 5%


class SimulatorSource(MarketDataSource):
    """
    Generates realistic stock prices using geometric Brownian motion
    with sector correlation and random shock events.
    """

    def __init__(self, tickers: list[TickerConfig] | None = None) -> None:
        self._configs: dict[str, TickerConfig] = {}
        self._current_prices: dict[str, float] = {}
        self._running = False
        self._task: asyncio.Task | None = None
        self._cache: PriceCache | None = None

        for cfg in (tickers or DEFAULT_TICKERS):
            self._configs[cfg.ticker] = cfg
            self._current_prices[cfg.ticker] = cfg.seed_price

    async def start(self, cache: PriceCache) -> None:
        self._cache = cache
        self._running = True

        # Seed initial prices into the cache
        now = datetime.now(timezone.utc)
        for ticker, price in self._current_prices.items():
            await cache.update(ticker, price, now)

        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def add_ticker(self, ticker: str) -> None:
        """
        Dynamically add a ticker to the simulator.

        Seeds at a plausible price ($50-$500 range) with default volatility.
        """
        if ticker in self._configs:
            return

        seed_price = random.uniform(50.0, 500.0)
        config = TickerConfig(
            ticker=ticker,
            seed_price=seed_price,
            volatility=0.30,
            sector="tech",  # default; doesn't matter much for a single add
        )
        self._configs[ticker] = config
        self._current_prices[ticker] = seed_price

        # If already running, seed the cache immediately
        if self._cache:
            asyncio.create_task(
                self._cache.update(ticker, seed_price, datetime.now(timezone.utc))
            )

    def remove_ticker(self, ticker: str) -> None:
        self._configs.pop(ticker, None)
        self._current_prices.pop(ticker, None)
        if self._cache:
            asyncio.create_task(self._cache.remove(ticker))

    def get_tracked_tickers(self) -> list[str]:
        return list(self._configs.keys())

    async def _run_loop(self) -> None:
        """Main simulation loop — runs every TICK_INTERVAL seconds."""
        while self._running:
            await self._generate_tick()
            await asyncio.sleep(TICK_INTERVAL)

    async def _generate_tick(self) -> None:
        """
        Generate one round of correlated price moves for all tracked tickers.
        """
        tickers = list(self._configs.keys())
        n = len(tickers)
        if n == 0:
            return

        # Build correlation matrix
        corr_matrix = np.eye(n)
        sectors = [self._configs[t].sector for t in tickers]
        for i in range(n):
            for j in range(i + 1, n):
                key = (sectors[i], sectors[j])
                rho = SECTOR_CORRELATION.get(key, DEFAULT_CROSS_CORRELATION)
                corr_matrix[i, j] = rho
                corr_matrix[j, i] = rho

        # Cholesky decomposition for correlated normals
        try:
            L = np.linalg.cholesky(corr_matrix)
        except np.linalg.LinAlgError:
            # Fallback: if matrix isn't positive definite (shouldn't happen
            # with our correlations), use independent normals
            L = np.eye(n)

        z_independent = np.random.standard_normal(n)
        z_correlated = L @ z_independent

        now = datetime.now(timezone.utc)

        for i, ticker in enumerate(tickers):
            cfg = self._configs[ticker]
            S = self._current_prices[ticker]

            # GBM step
            mu = cfg.drift
            sigma = cfg.volatility
            z = z_correlated[i]
            new_price = S * math.exp((mu - 0.5 * sigma**2) * DT + sigma * math.sqrt(DT) * z)

            # Random event: sudden shock
            if random.random() < EVENT_PROBABILITY:
                shock_pct = random.uniform(EVENT_MIN_PCT, EVENT_MAX_PCT)
                shock_direction = random.choice([-1, 1])
                new_price *= (1 + shock_direction * shock_pct)

            # Clamp to prevent negative/zero prices
            new_price = max(new_price, 0.01)

            self._current_prices[ticker] = new_price
            await self._cache.update(ticker, new_price, now)
