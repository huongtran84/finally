"""Unit tests for the PriceCache."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.market.cache import PriceCache
from app.market.models import PriceUpdate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_update(
    ticker: str = "VNM",
    price: float = 75_000.0,
    previous_price: float = 74_500.0,
) -> PriceUpdate:
    return PriceUpdate(
        ticker=ticker,
        price=price,
        previous_price=previous_price,
        open=74_000.0,
        high=75_500.0,
        low=73_500.0,
        volume=1_000_000,
        timestamp=datetime.now(timezone.utc).isoformat(),
        basic_price=74_000.0,
        ceiling_price=79_180.0,
        floor_price=68_820.0,
        exchange="HOSE",
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestPriceCacheConstruction:
    def test_default_history_size(self):
        cache = PriceCache()
        assert cache._history_size == 50

    def test_custom_history_size(self):
        cache = PriceCache(history_size=10)
        assert cache._history_size == 10

    def test_initial_state_empty(self):
        cache = PriceCache()
        assert cache.get_all() == {}
        assert cache.tickers() == []


# ---------------------------------------------------------------------------
# update / get
# ---------------------------------------------------------------------------

class TestPriceCacheUpdate:
    def test_get_returns_none_for_unknown_ticker(self):
        cache = PriceCache()
        assert cache.get("UNKNOWN") is None

    def test_get_returns_latest_update(self):
        cache = PriceCache()
        update = _make_update("VNM", price=75_000.0)
        cache.update(update)
        assert cache.get("VNM") == update

    def test_get_returns_most_recent_when_updated_twice(self):
        cache = PriceCache()
        u1 = _make_update("VNM", price=75_000.0)
        u2 = _make_update("VNM", price=76_000.0)
        cache.update(u1)
        cache.update(u2)
        assert cache.get("VNM").price == 76_000.0

    def test_get_all_returns_snapshot(self):
        cache = PriceCache()
        cache.update(_make_update("VNM", price=75_000.0))
        cache.update(_make_update("VCB", price=92_000.0))
        all_prices = cache.get_all()
        assert set(all_prices.keys()) == {"VNM", "VCB"}
        assert all_prices["VNM"].price == 75_000.0
        assert all_prices["VCB"].price == 92_000.0

    def test_get_all_returns_independent_copy(self):
        """Mutating the returned dict should not affect the cache."""
        cache = PriceCache()
        cache.update(_make_update("VNM"))
        snapshot = cache.get_all()
        snapshot["INJECTED"] = _make_update("INJECTED")
        assert "INJECTED" not in cache.get_all()


# ---------------------------------------------------------------------------
# session_open
# ---------------------------------------------------------------------------

class TestPriceCacheSessionOpen:
    def test_session_open_set_on_first_update(self):
        cache = PriceCache()
        cache.update(_make_update("VNM", price=75_000.0))
        assert cache.get_session_open("VNM") == 75_000.0

    def test_session_open_not_changed_on_subsequent_updates(self):
        cache = PriceCache()
        cache.update(_make_update("VNM", price=75_000.0))
        cache.update(_make_update("VNM", price=76_000.0))
        # session_open should still be the first price
        assert cache.get_session_open("VNM") == 75_000.0

    def test_session_open_returns_zero_for_unknown_ticker(self):
        cache = PriceCache()
        assert cache.get_session_open("UNKNOWN") == 0.0

    def test_get_all_session_opens(self):
        cache = PriceCache()
        cache.update(_make_update("VNM", price=75_000.0))
        cache.update(_make_update("VCB", price=92_000.0))
        opens = cache.get_all_session_opens()
        assert opens == {"VNM": 75_000.0, "VCB": 92_000.0}


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------

class TestPriceCacheHistory:
    def test_history_empty_for_unknown_ticker(self):
        cache = PriceCache()
        assert cache.get_history("UNKNOWN") == []

    def test_history_contains_updates_in_order(self):
        cache = PriceCache()
        prices = [75_000.0, 75_100.0, 75_200.0]
        for p in prices:
            cache.update(_make_update("VNM", price=p))
        history = cache.get_history("VNM")
        assert [u.price for u in history] == prices

    def test_history_respects_limit_parameter(self):
        cache = PriceCache()
        for i in range(10):
            cache.update(_make_update("VNM", price=float(75_000 + i * 100)))
        assert len(cache.get_history("VNM", limit=3)) == 3

    def test_history_limit_returns_most_recent(self):
        cache = PriceCache()
        for i in range(5):
            cache.update(_make_update("VNM", price=float(75_000 + i * 100)))
        history = cache.get_history("VNM", limit=3)
        assert history[0].price == 75_200.0
        assert history[-1].price == 75_400.0

    def test_history_capped_at_history_size(self):
        cache = PriceCache(history_size=5)
        for i in range(10):
            cache.update(_make_update("VNM", price=float(75_000 + i * 100)))
        history = cache.get_history("VNM")
        assert len(history) == 5
        # Most-recent 5 prices preserved
        assert history[-1].price == 75_900.0


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------

class TestPriceCacheRemove:
    def test_remove_clears_price(self):
        cache = PriceCache()
        cache.update(_make_update("VNM"))
        cache.remove("VNM")
        assert cache.get("VNM") is None

    def test_remove_clears_history(self):
        cache = PriceCache()
        cache.update(_make_update("VNM"))
        cache.remove("VNM")
        assert cache.get_history("VNM") == []

    def test_remove_clears_session_open(self):
        cache = PriceCache()
        cache.update(_make_update("VNM"))
        cache.remove("VNM")
        assert cache.get_session_open("VNM") == 0.0

    def test_remove_nonexistent_ticker_does_not_raise(self):
        cache = PriceCache()
        cache.remove("NONEXISTENT")  # Should not raise

    def test_remove_leaves_other_tickers_intact(self):
        cache = PriceCache()
        cache.update(_make_update("VNM"))
        cache.update(_make_update("VCB"))
        cache.remove("VNM")
        assert cache.get("VCB") is not None
        assert "VCB" in cache.get_all()


# ---------------------------------------------------------------------------
# is_tracking / tickers
# ---------------------------------------------------------------------------

class TestPriceCacheTracking:
    def test_is_tracking_false_before_update(self):
        cache = PriceCache()
        assert not cache.is_tracking("VNM")

    def test_is_tracking_true_after_update(self):
        cache = PriceCache()
        cache.update(_make_update("VNM"))
        assert cache.is_tracking("VNM")

    def test_is_tracking_false_after_remove(self):
        cache = PriceCache()
        cache.update(_make_update("VNM"))
        cache.remove("VNM")
        assert not cache.is_tracking("VNM")

    def test_tickers_reflects_all_tracked(self):
        cache = PriceCache()
        cache.update(_make_update("VNM"))
        cache.update(_make_update("VCB"))
        assert set(cache.tickers()) == {"VNM", "VCB"}

    def test_tickers_excludes_removed(self):
        cache = PriceCache()
        cache.update(_make_update("VNM"))
        cache.update(_make_update("VCB"))
        cache.remove("VNM")
        assert cache.tickers() == ["VCB"]


# ---------------------------------------------------------------------------
# Thread safety (basic smoke test)
# ---------------------------------------------------------------------------

class TestPriceCacheThreadSafety:
    def test_concurrent_updates_do_not_raise(self):
        import threading
        cache = PriceCache()
        errors: list[Exception] = []

        def write(ticker: str, price: float) -> None:
            try:
                for _ in range(50):
                    cache.update(_make_update(ticker, price=price))
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=write, args=(f"T{i}", float(i * 1000)))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
