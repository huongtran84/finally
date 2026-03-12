"""Unit tests for PriceUpdate model and related helpers."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.market.models import (
    DEFAULT_STOCKS,
    DEFAULT_STOCKS_MAP,
    PRICE_LIMITS,
    PriceUpdate,
    StockConfig,
    TickerState,
)


def _make_update(
    ticker: str = "VNM",
    price: float = 75_000.0,
    previous_price: float = 74_500.0,
    open_price: float = 74_000.0,
    basic_price: float = 74_000.0,
    ceiling_price: float = 79_180.0,
    floor_price: float = 68_820.0,
) -> PriceUpdate:
    return PriceUpdate(
        ticker=ticker,
        price=price,
        previous_price=previous_price,
        open=open_price,
        high=76_000.0,
        low=73_000.0,
        volume=1_000_000,
        timestamp=datetime.now(timezone.utc).isoformat(),
        basic_price=basic_price,
        ceiling_price=ceiling_price,
        floor_price=floor_price,
        exchange="HOSE",
    )


class TestPriceUpdateProperties:
    def test_session_change_pct_positive(self):
        # price=75_000, open=74_000 → +1.35%
        update = _make_update(price=75_000.0, open_price=74_000.0)
        assert update.session_change_pct == pytest.approx(1.35, abs=0.01)

    def test_session_change_pct_negative(self):
        update = _make_update(price=73_000.0, open_price=74_000.0)
        assert update.session_change_pct < 0

    def test_session_change_pct_zero_when_open_is_zero(self):
        update = _make_update(open_price=0.0)
        assert update.session_change_pct == 0.0

    def test_is_ceiling_true_at_ceiling(self):
        update = _make_update(price=79_180.0, ceiling_price=79_180.0)
        assert update.is_ceiling is True

    def test_is_ceiling_false_below_ceiling(self):
        update = _make_update(price=75_000.0, ceiling_price=79_180.0)
        assert update.is_ceiling is False

    def test_is_floor_true_at_floor(self):
        update = _make_update(price=68_820.0, floor_price=68_820.0)
        assert update.is_floor is True

    def test_is_floor_false_above_floor(self):
        update = _make_update(price=75_000.0, floor_price=68_820.0)
        assert update.is_floor is False


class TestPriceUpdateToSseDict:
    def test_keys_present(self):
        update = _make_update()
        sse = update.to_sse_dict(session_open=74_000.0)
        expected_keys = {
            "ticker", "price", "previousPrice", "open", "high", "low",
            "volume", "timestamp", "basicPrice", "ceilingPrice", "floorPrice",
            "exchange", "sessionChangePct", "isCeiling", "isFloor",
        }
        assert set(sse.keys()) == expected_keys

    def test_session_change_pct_computed_from_session_open(self):
        update = _make_update(price=75_000.0)
        sse = update.to_sse_dict(session_open=74_000.0)
        expected = round((75_000.0 - 74_000.0) / 74_000.0 * 100, 2)
        assert sse["sessionChangePct"] == pytest.approx(expected, abs=0.01)

    def test_session_change_pct_zero_when_session_open_zero(self):
        update = _make_update()
        sse = update.to_sse_dict(session_open=0.0)
        assert sse["sessionChangePct"] == 0.0

    def test_is_ceiling_reflected(self):
        update = _make_update(price=79_180.0, ceiling_price=79_180.0)
        sse = update.to_sse_dict(session_open=74_000.0)
        assert sse["isCeiling"] is True

    def test_is_floor_reflected(self):
        update = _make_update(price=68_820.0, floor_price=68_820.0)
        sse = update.to_sse_dict(session_open=74_000.0)
        assert sse["isFloor"] is True


class TestDefaultStocks:
    def test_default_stocks_count(self):
        assert len(DEFAULT_STOCKS) == 10

    def test_all_default_tickers_present_in_map(self):
        expected = {"VNM", "VCB", "VIC", "HPG", "FPT", "MWG", "TCB", "VHM", "GAS", "MSN"}
        assert set(DEFAULT_STOCKS_MAP.keys()) == expected

    def test_all_default_stocks_on_hose(self):
        for stock in DEFAULT_STOCKS:
            assert stock.exchange == "HOSE", f"{stock.ticker} is not on HOSE"

    def test_initial_prices_are_positive(self):
        for stock in DEFAULT_STOCKS:
            assert stock.initial_price > 0, f"{stock.ticker} has non-positive initial price"

    def test_mu_and_sigma_are_positive(self):
        for stock in DEFAULT_STOCKS:
            assert stock.mu > 0, f"{stock.ticker} mu <= 0"
            assert stock.sigma > 0, f"{stock.ticker} sigma <= 0"

    def test_vnm_config(self):
        assert DEFAULT_STOCKS_MAP["VNM"].initial_price == 75_000
        assert DEFAULT_STOCKS_MAP["VNM"].exchange == "HOSE"
        assert DEFAULT_STOCKS_MAP["VNM"].sector == "consumer"


class TestPriceLimits:
    def test_hose_limit(self):
        assert PRICE_LIMITS["HOSE"] == 0.07

    def test_hnx_limit(self):
        assert PRICE_LIMITS["HNX"] == 0.10

    def test_upcom_limit(self):
        assert PRICE_LIMITS["UPCOM"] == 0.15
