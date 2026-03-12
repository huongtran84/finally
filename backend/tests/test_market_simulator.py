"""Unit tests for the market data simulator."""
from __future__ import annotations

import asyncio
import math
from unittest.mock import patch

import pytest

from app.market.cache import PriceCache
from app.market.models import DEFAULT_STOCKS_MAP, PRICE_LIMITS, StockConfig
from app.market.simulator import (
    SimulatorDataSource,
    clamp_price,
    generate_correlated_returns,
    maybe_apply_jump,
    round_vnd,
)


# ---------------------------------------------------------------------------
# Pure helper function tests
# ---------------------------------------------------------------------------

class TestRoundVnd:
    def test_rounds_down(self):
        assert round_vnd(75_049.0) == 75_000.0

    def test_rounds_up(self):
        assert round_vnd(75_050.0) == 75_100.0

    def test_already_multiple(self):
        assert round_vnd(75_000.0) == 75_000.0

    def test_zero(self):
        assert round_vnd(0.0) == 0.0


class TestClampPrice:
    def test_price_within_limits_unchanged(self):
        # HOSE: ±7% → ceiling=107, floor=93 for basic=100
        assert clamp_price(100.0, 100.0, "HOSE") == 100.0

    def test_price_above_ceiling_clamped(self):
        # ceiling = 75_000 * 1.07 = 80_250
        result = clamp_price(85_000.0, 75_000.0, "HOSE")
        assert result == 75_000.0 * 1.07

    def test_price_below_floor_clamped(self):
        # floor = 75_000 * 0.93 = 69_750
        result = clamp_price(60_000.0, 75_000.0, "HOSE")
        assert result == 75_000.0 * 0.93

    def test_hose_limit_7pct(self):
        limit = PRICE_LIMITS["HOSE"]
        assert limit == 0.07
        ceiling = 100.0 * (1 + limit)
        floor_ = 100.0 * (1 - limit)
        assert clamp_price(ceiling + 1, 100.0, "HOSE") == ceiling
        assert clamp_price(floor_ - 1, 100.0, "HOSE") == floor_

    def test_hnx_limit_10pct(self):
        limit = PRICE_LIMITS["HNX"]
        assert limit == 0.10
        ceiling = 100.0 * (1 + limit)
        floor_ = 100.0 * (1 - limit)
        assert clamp_price(ceiling + 1, 100.0, "HNX") == ceiling
        assert clamp_price(floor_ - 1, 100.0, "HNX") == floor_

    def test_upcom_limit_15pct(self):
        limit = PRICE_LIMITS["UPCOM"]
        assert limit == 0.15
        ceiling = 100.0 * (1 + limit)
        floor_ = 100.0 * (1 - limit)
        assert clamp_price(ceiling + 1, 100.0, "UPCOM") == ceiling
        assert clamp_price(floor_ - 1, 100.0, "UPCOM") == floor_

    def test_unknown_exchange_defaults_to_hose(self):
        # Should fall back to 7% for unknown exchange codes
        result_unknown = clamp_price(150.0, 100.0, "UNKNOWN")
        result_hose = clamp_price(150.0, 100.0, "HOSE")
        assert result_unknown == result_hose


class TestMaybeApplyJump:
    def test_jump_not_applied_when_random_above_threshold(self):
        with patch("app.market.simulator.random.random", return_value=0.999):
            result = maybe_apply_jump(100.0)
        assert result == 100.0

    def test_jump_applied_when_random_below_threshold(self):
        with (
            patch("app.market.simulator.random.random", return_value=0.0),
            patch("app.market.simulator.random.uniform", return_value=0.03),
            patch("app.market.simulator.random.choice", return_value=1),
        ):
            result = maybe_apply_jump(100.0)
        assert result == pytest.approx(103.0)

    def test_jump_can_be_negative(self):
        with (
            patch("app.market.simulator.random.random", return_value=0.0),
            patch("app.market.simulator.random.uniform", return_value=0.03),
            patch("app.market.simulator.random.choice", return_value=-1),
        ):
            result = maybe_apply_jump(100.0)
        assert result == pytest.approx(97.0)


class TestGenerateCorrelatedReturns:
    def _banking_configs(self) -> list[StockConfig]:
        """Two banking stocks — should be highly correlated."""
        return [DEFAULT_STOCKS_MAP["VCB"], DEFAULT_STOCKS_MAP["TCB"]]

    def test_returns_entry_for_each_stock(self):
        stocks = list(DEFAULT_STOCKS_MAP.values())
        returns = generate_correlated_returns(stocks, dt=1e-6)
        assert set(returns.keys()) == {s.ticker for s in stocks}

    def test_returns_are_finite(self):
        stocks = list(DEFAULT_STOCKS_MAP.values())
        returns = generate_correlated_returns(stocks, dt=1e-6)
        for ticker, r in returns.items():
            assert math.isfinite(r), f"Return for {ticker} is not finite: {r}"

    def test_single_stock_does_not_raise(self):
        stocks = [DEFAULT_STOCKS_MAP["VNM"]]
        returns = generate_correlated_returns(stocks, dt=1e-6)
        assert "VNM" in returns


# ---------------------------------------------------------------------------
# SimulatorDataSource tests
# ---------------------------------------------------------------------------

class TestSimulatorDataSource:
    def _make_simulator(self, tickers: list[str] | None = None) -> tuple[PriceCache, SimulatorDataSource]:
        cache = PriceCache()
        sim = SimulatorDataSource(cache, tickers or ["VNM", "VCB"])
        return cache, sim

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def test_is_not_tracking_before_start(self):
        _, sim = self._make_simulator(["VNM"])
        # Tickers are not initialised until start() is called
        assert not sim.is_tracking("VNM")

    @pytest.mark.asyncio
    async def test_tickers_tracked_after_start(self):
        _, sim = self._make_simulator(["VNM", "VCB"])
        await sim.start()
        try:
            assert sim.is_tracking("VNM")
            assert sim.is_tracking("VCB")
        finally:
            await sim.stop()

    @pytest.mark.asyncio
    async def test_initial_prices_seeded_after_start(self):
        cache, sim = self._make_simulator(["VNM"])
        await sim.start()
        try:
            update = sim.get_price("VNM")
            assert update is not None
            assert update.price > 0
        finally:
            await sim.stop()

    # ------------------------------------------------------------------
    # Price invariants
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_prices_are_positive(self):
        cache, sim = self._make_simulator(["VNM", "VCB", "HPG"])
        await sim.start()
        try:
            # Run a few ticks
            for _ in range(5):
                sim._tick()
            for ticker in ["VNM", "VCB", "HPG"]:
                update = sim.get_price(ticker)
                assert update is not None
                assert update.price > 0, f"{ticker} price is not positive: {update.price}"
        finally:
            await sim.stop()

    @pytest.mark.asyncio
    async def test_prices_are_multiples_of_100(self):
        cache, sim = self._make_simulator(["VNM", "VCB", "HPG"])
        await sim.start()
        try:
            for _ in range(10):
                sim._tick()
            for ticker in ["VNM", "VCB", "HPG"]:
                update = sim.get_price(ticker)
                assert update is not None
                assert update.price % 100 == 0, (
                    f"{ticker} price {update.price} is not a multiple of 100"
                )
        finally:
            await sim.stop()

    @pytest.mark.asyncio
    async def test_prices_within_price_limits(self):
        cache, sim = self._make_simulator(["VNM"])
        await sim.start()
        try:
            for _ in range(20):
                sim._tick()
            update = sim.get_price("VNM")
            assert update is not None
            assert update.price <= update.ceiling_price, (
                f"Price {update.price} exceeds ceiling {update.ceiling_price}"
            )
            assert update.price >= update.floor_price, (
                f"Price {update.price} is below floor {update.floor_price}"
            )
        finally:
            await sim.stop()

    @pytest.mark.asyncio
    async def test_ceiling_and_floor_computed_correctly(self):
        cache, sim = self._make_simulator(["VNM"])
        await sim.start()
        try:
            state = sim._states["VNM"]
            expected_ceiling = round_vnd(state.basic_price * 1.07)
            expected_floor = round_vnd(state.basic_price * 0.93)
            update = sim.get_price("VNM")
            assert update.ceiling_price == expected_ceiling
            assert update.floor_price == expected_floor
        finally:
            await sim.stop()

    @pytest.mark.asyncio
    async def test_volume_is_positive_multiple_of_100(self):
        cache, sim = self._make_simulator(["VNM"])
        await sim.start()
        try:
            for _ in range(5):
                sim._tick()
            update = sim.get_price("VNM")
            assert update.volume > 0
            assert update.volume % 100 == 0
        finally:
            await sim.stop()

    # ------------------------------------------------------------------
    # add_ticker / remove_ticker
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_add_ticker_seeds_price_immediately(self):
        cache, sim = self._make_simulator([])
        await sim.start()
        try:
            sim.add_ticker("FPT")
            assert sim.is_tracking("FPT")
            update = sim.get_price("FPT")
            assert update is not None
            assert update.price > 0
        finally:
            await sim.stop()

    @pytest.mark.asyncio
    async def test_add_ticker_twice_does_not_reset_price(self):
        cache, sim = self._make_simulator(["VNM"])
        await sim.start()
        try:
            first_price = sim.get_price("VNM").price
            sim.add_ticker("VNM")  # Should be a no-op
            assert sim.get_price("VNM").price == first_price
        finally:
            await sim.stop()

    @pytest.mark.asyncio
    async def test_remove_ticker_clears_tracking(self):
        cache, sim = self._make_simulator(["VNM"])
        await sim.start()
        try:
            sim.remove_ticker("VNM")
            assert not sim.is_tracking("VNM")
            assert sim.get_price("VNM") is None
        finally:
            await sim.stop()

    @pytest.mark.asyncio
    async def test_remove_nonexistent_ticker_does_not_raise(self):
        _, sim = self._make_simulator(["VNM"])
        await sim.start()
        try:
            sim.remove_ticker("NONEXISTENT")  # Should not raise
        finally:
            await sim.stop()

    # ------------------------------------------------------------------
    # Unknown ticker handling
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_unknown_ticker_gets_plausible_defaults(self):
        cache, sim = self._make_simulator([])
        await sim.start()
        try:
            sim.add_ticker("XYZ")
            update = sim.get_price("XYZ")
            assert update is not None
            assert 20_000 <= update.price <= 200_000
            assert update.exchange == "HOSE"
        finally:
            await sim.stop()

    # ------------------------------------------------------------------
    # get_history / get_session_open
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_history_grows_with_ticks(self):
        cache, sim = self._make_simulator(["VNM"])
        await sim.start()
        try:
            initial_len = len(sim.get_history("VNM"))
            sim._tick()
            assert len(sim.get_history("VNM")) > initial_len
        finally:
            await sim.stop()

    @pytest.mark.asyncio
    async def test_get_session_open_set_on_start(self):
        cache, sim = self._make_simulator(["VNM"])
        await sim.start()
        try:
            session_open = sim.get_session_open("VNM")
            assert session_open > 0
        finally:
            await sim.stop()

    # ------------------------------------------------------------------
    # stop / cancellation
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_stop_cancels_background_task(self):
        _, sim = self._make_simulator(["VNM"])
        await sim.start()
        assert sim._task is not None
        await sim.stop()
        assert sim._task.cancelled() or sim._task.done()

    @pytest.mark.asyncio
    async def test_stop_idempotent_when_not_started(self):
        _, sim = self._make_simulator(["VNM"])
        await sim.stop()  # Should not raise

    # ------------------------------------------------------------------
    # get_all_prices
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_all_prices_contains_all_tracked_tickers(self):
        cache, sim = self._make_simulator(["VNM", "VCB", "FPT"])
        await sim.start()
        try:
            prices = sim.get_all_prices()
            assert set(prices.keys()) == {"VNM", "VCB", "FPT"}
        finally:
            await sim.stop()
