"""GBM-based market data simulator for Vietnamese stocks."""
from __future__ import annotations

import asyncio
import math
import random
from datetime import datetime, timezone

import numpy as np

from app.market.base import MarketDataSource
from app.market.cache import PriceCache
from app.market.models import (
    DEFAULT_STOCKS_MAP,
    PRICE_LIMITS,
    PriceUpdate,
    StockConfig,
    TickerState,
)

# ---------------------------------------------------------------------------
# Simulation constants
# ---------------------------------------------------------------------------

#: Seconds between each price update tick.
UPDATE_INTERVAL: float = 0.5

#: Annualised time-step: 0.5s expressed as a fraction of a trading year
#: (252 days × 6.5 hours × 3600 seconds).
DT: float = UPDATE_INTERVAL / (252 * 6.5 * 3600)

#: Fraction of a stock's movement driven by its sector factor.
SECTOR_CORRELATION: float = 0.6

#: Probability per tick per stock that a random jump is injected.
JUMP_PROBABILITY: float = 0.002

#: Minimum jump magnitude (as a fraction of current price).
JUMP_MAGNITUDE_MIN: float = 0.02

#: Maximum jump magnitude.
JUMP_MAGNITUDE_MAX: float = 0.05


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def generate_correlated_returns(
    stocks: list[StockConfig], dt: float
) -> dict[str, float]:
    """Generate GBM log-returns with intra-sector correlation.

    Each stock's return is a weighted combination of a shared sector shock
    and an idiosyncratic (stock-specific) shock.  The weight is given by
    ``SECTOR_CORRELATION``.

    Args:
        stocks: List of stock configurations to generate returns for.
        dt: Annualised time-step size.

    Returns:
        Mapping of ticker → log-return.
    """
    sectors = {s.sector for s in stocks}
    sector_shocks: dict[str, float] = {
        sector: float(np.random.normal()) for sector in sectors
    }

    returns: dict[str, float] = {}
    for stock in stocks:
        sector_z = sector_shocks[stock.sector]
        idio_z = float(np.random.normal())
        # Blend sector and idiosyncratic components
        z = (
            SECTOR_CORRELATION * sector_z
            + math.sqrt(1.0 - SECTOR_CORRELATION ** 2) * idio_z
        )
        drift = (stock.mu - 0.5 * stock.sigma ** 2) * dt
        diffusion = stock.sigma * math.sqrt(dt) * z
        returns[stock.ticker] = drift + diffusion
    return returns


def clamp_price(new_price: float, basic_price: float, exchange: str) -> float:
    """Clamp *new_price* within the exchange-specific daily price limits.

    Vietnamese exchanges enforce hard limits relative to the reference price
    (``basic_price``):

    * HOSE: ±7 %
    * HNX:  ±10 %
    * UPCOM: ±15 %

    Args:
        new_price: Candidate new price.
        basic_price: Reference / opening price for the day.
        exchange: One of ``"HOSE"``, ``"HNX"``, ``"UPCOM"``.

    Returns:
        Price clamped to [floor, ceiling].
    """
    limit = PRICE_LIMITS.get(exchange, 0.07)
    ceiling = basic_price * (1.0 + limit)
    floor = basic_price * (1.0 - limit)
    return max(floor, min(ceiling, new_price))


def maybe_apply_jump(price: float) -> float:
    """Randomly inject a price jump to simulate news/event-driven moves.

    Args:
        price: Current price.

    Returns:
        Price after optionally applying a random jump.
    """
    if random.random() < JUMP_PROBABILITY:
        magnitude = random.uniform(JUMP_MAGNITUDE_MIN, JUMP_MAGNITUDE_MAX)
        direction = random.choice([-1, 1])
        return price * (1.0 + direction * magnitude)
    return price


def round_vnd(price: float) -> float:
    """Round a VND price to the nearest 100 VND.

    Vietnamese stocks on HOSE trade in multiples of 100 VND.
    """
    return round(price / 100) * 100


# ---------------------------------------------------------------------------
# Simulator implementation
# ---------------------------------------------------------------------------

class SimulatorDataSource(MarketDataSource):
    """Simulates Vietnamese stock prices using Geometric Brownian Motion.

    Features:
    * GBM with configurable drift (mu) and volatility (sigma) per stock.
    * Intra-sector correlation — stocks in the same sector move together.
    * Random jump events to simulate news-driven price moves.
    * Price-limit enforcement (ceiling/floor) per Vietnamese exchange rules.
    * Price rounded to the nearest 100 VND.
    * Runs as an asyncio background task; safe to start/stop at runtime.
    """

    def __init__(self, cache: PriceCache, tickers: list[str]) -> None:
        self._cache = cache
        self._running = False
        self._task: asyncio.Task | None = None
        # Internal per-ticker simulation state
        self._states: dict[str, TickerState] = {}
        # Stock configs keyed by ticker (pre-loaded with defaults, extended on demand)
        self._configs: dict[str, StockConfig] = dict(DEFAULT_STOCKS_MAP)
        self._initial_tickers = list(tickers)

    # ------------------------------------------------------------------
    # MarketDataSource interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Seed all initial tickers and start the simulation loop."""
        self._running = True
        for ticker in self._initial_tickers:
            self._init_ticker(ticker)
        self._task = asyncio.create_task(self._simulate_loop())

    async def stop(self) -> None:
        """Cancel the simulation background task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def get_price(self, ticker: str) -> PriceUpdate | None:
        return self._cache.get(ticker)

    def get_all_prices(self) -> dict[str, PriceUpdate]:
        return self._cache.get_all()

    def get_history(self, ticker: str, limit: int = 50) -> list[PriceUpdate]:
        return self._cache.get_history(ticker, limit)

    def get_session_open(self, ticker: str) -> float:
        return self._cache.get_session_open(ticker)

    def add_ticker(self, ticker: str) -> None:
        """Start simulating a new ticker (seeds an initial price immediately)."""
        if ticker not in self._states:
            self._init_ticker(ticker)

    def remove_ticker(self, ticker: str) -> None:
        """Stop simulating a ticker and remove it from the price cache."""
        self._states.pop(ticker, None)
        self._cache.remove(ticker)

    def is_tracking(self, ticker: str) -> bool:
        return ticker in self._states

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _init_ticker(self, ticker: str) -> None:
        """Seed initial price and state for *ticker*."""
        config = self._configs.get(ticker)
        if config is None:
            # Unknown ticker: generate plausible defaults
            config = StockConfig(
                ticker=ticker,
                name=ticker,
                exchange="HOSE",
                sector="other",
                initial_price=random.uniform(20_000, 150_000),
                mu=0.05,
                sigma=0.30,
            )
            self._configs[ticker] = config

        # Small random jitter on initial price (±2 %)
        jitter = random.uniform(-0.02, 0.02)
        price = round_vnd(config.initial_price * (1.0 + jitter))

        state = TickerState(
            config=config,
            current_price=price,
            previous_price=price,
            session_open=price,
            basic_price=price,
            day_open=price,
            day_high=price,
            day_low=price,
            day_volume=0,
        )
        self._states[ticker] = state
        self._write_to_cache(state)

    async def _simulate_loop(self) -> None:
        """Main simulation loop — tick every UPDATE_INTERVAL seconds."""
        while self._running:
            self._tick()
            await asyncio.sleep(UPDATE_INTERVAL)

    def _tick(self) -> None:
        """Advance all tickers by one time step."""
        active_configs = [
            self._configs[t]
            for t in list(self._states)
            if t in self._configs
        ]
        if not active_configs:
            return

        log_returns = generate_correlated_returns(active_configs, DT)

        for ticker, log_return in log_returns.items():
            state = self._states.get(ticker)
            if state is None:
                continue

            # Apply GBM step
            new_price = state.current_price * math.exp(log_return)

            # Optionally inject a random jump
            new_price = maybe_apply_jump(new_price)

            # Enforce Vietnamese exchange price limits
            new_price = clamp_price(
                new_price, state.basic_price, state.config.exchange
            )

            # Round to nearest 100 VND
            new_price = round_vnd(new_price)

            # Random volume increment (100–5000 lots of 100 shares each)
            vol_increment = random.randint(100, 5_000) * 100

            # Update in-memory state
            state.previous_price = state.current_price
            state.current_price = new_price
            state.day_high = max(state.day_high, new_price)
            state.day_low = min(state.day_low, new_price)
            state.day_volume += vol_increment

            self._write_to_cache(state)

    def _write_to_cache(self, state: TickerState) -> None:
        """Publish the current state of *state* to the shared price cache."""
        exchange = state.config.exchange
        limit = PRICE_LIMITS.get(exchange, 0.07)
        ceiling = round_vnd(state.basic_price * (1.0 + limit))
        floor_ = round_vnd(state.basic_price * (1.0 - limit))

        update = PriceUpdate(
            ticker=state.config.ticker,
            price=state.current_price,
            previous_price=state.previous_price,
            open=state.day_open,
            high=state.day_high,
            low=state.day_low,
            volume=state.day_volume,
            timestamp=datetime.now(timezone.utc).isoformat(),
            basic_price=state.basic_price,
            ceiling_price=ceiling,
            floor_price=floor_,
            exchange=exchange,
        )
        self._cache.update(update)
