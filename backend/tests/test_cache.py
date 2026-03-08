import pytest
from datetime import datetime, timezone

from app.market_data.cache import PriceCache


@pytest.mark.asyncio
async def test_first_update_sets_session_start():
    cache = PriceCache()
    tick = await cache.update("AAPL", 192.0, datetime.now(timezone.utc))
    assert tick.session_change_pct == 0.0
    assert tick.direction == "flat"
    assert tick.previous_price == 192.0


@pytest.mark.asyncio
async def test_second_update_computes_direction_up():
    cache = PriceCache()
    now = datetime.now(timezone.utc)
    await cache.update("AAPL", 192.0, now)
    tick = await cache.update("AAPL", 193.0, now)
    assert tick.direction == "up"
    assert tick.previous_price == 192.0
    assert tick.session_change_pct > 0


@pytest.mark.asyncio
async def test_second_update_computes_direction_down():
    cache = PriceCache()
    now = datetime.now(timezone.utc)
    await cache.update("AAPL", 192.0, now)
    tick = await cache.update("AAPL", 191.0, now)
    assert tick.direction == "down"
    assert tick.previous_price == 192.0
    assert tick.session_change_pct < 0


@pytest.mark.asyncio
async def test_session_change_pct_calculation():
    cache = PriceCache()
    now = datetime.now(timezone.utc)
    await cache.update("AAPL", 100.0, now)
    tick = await cache.update("AAPL", 110.0, now)
    assert abs(tick.session_change_pct - 10.0) < 0.01


@pytest.mark.asyncio
async def test_history_limited_to_50():
    cache = PriceCache()
    now = datetime.now(timezone.utc)
    for i in range(60):
        await cache.update("AAPL", 100.0 + i, now)

    history = await cache.get_history("AAPL")
    assert len(history.prices) == 50


@pytest.mark.asyncio
async def test_history_keeps_most_recent():
    """After 60 updates, history should contain the 50 most recent prices."""
    cache = PriceCache()
    now = datetime.now(timezone.utc)
    for i in range(60):
        await cache.update("AAPL", float(100 + i), now)

    history = await cache.get_history("AAPL")
    # The oldest price in history should be index 10 (100+10=110)
    assert history.prices[0].price == 110.0
    # The newest price should be index 59 (100+59=159)
    assert history.prices[-1].price == 159.0


@pytest.mark.asyncio
async def test_version_increments_on_update():
    cache = PriceCache()
    v0 = cache.version
    await cache.update("AAPL", 100.0, datetime.now(timezone.utc))
    assert cache.version == v0 + 1


@pytest.mark.asyncio
async def test_version_increments_on_remove():
    cache = PriceCache()
    await cache.update("AAPL", 100.0, datetime.now(timezone.utc))
    v_before = cache.version
    await cache.remove("AAPL")
    assert cache.version == v_before + 1


@pytest.mark.asyncio
async def test_remove_clears_everything():
    cache = PriceCache()
    await cache.update("AAPL", 100.0, datetime.now(timezone.utc))
    await cache.remove("AAPL")
    assert await cache.get_latest("AAPL") is None
    assert await cache.get_history("AAPL") is None
    assert await cache.get_price("AAPL") is None


@pytest.mark.asyncio
async def test_get_latest_returns_none_for_unknown():
    cache = PriceCache()
    assert await cache.get_latest("UNKNOWN") is None


@pytest.mark.asyncio
async def test_get_price_returns_none_for_unknown():
    cache = PriceCache()
    assert await cache.get_price("UNKNOWN") is None


@pytest.mark.asyncio
async def test_get_history_returns_none_for_unknown():
    cache = PriceCache()
    assert await cache.get_history("UNKNOWN") is None


@pytest.mark.asyncio
async def test_get_all_latest_multiple_tickers():
    cache = PriceCache()
    now = datetime.now(timezone.utc)
    await cache.update("AAPL", 192.0, now)
    await cache.update("MSFT", 420.0, now)

    all_prices = await cache.get_all_latest()
    assert "AAPL" in all_prices
    assert "MSFT" in all_prices
    assert all_prices["AAPL"].price == 192.0
    assert all_prices["MSFT"].price == 420.0


@pytest.mark.asyncio
async def test_get_all_history_multiple_tickers():
    cache = PriceCache()
    now = datetime.now(timezone.utc)
    await cache.update("AAPL", 192.0, now)
    await cache.update("MSFT", 420.0, now)

    all_history = await cache.get_all_history()
    assert "AAPL" in all_history
    assert "MSFT" in all_history
    assert all_history["AAPL"].session_start_price == 192.0
    assert all_history["MSFT"].session_start_price == 420.0


@pytest.mark.asyncio
async def test_price_rounded_to_2_decimal_places():
    cache = PriceCache()
    tick = await cache.update("AAPL", 192.123456, datetime.now(timezone.utc))
    assert tick.price == 192.12


@pytest.mark.asyncio
async def test_session_start_price_never_changes():
    """Session start price is set on first update and locked thereafter."""
    cache = PriceCache()
    now = datetime.now(timezone.utc)
    await cache.update("AAPL", 100.0, now)
    await cache.update("AAPL", 200.0, now)
    tick = await cache.update("AAPL", 150.0, now)
    # session_change_pct should be relative to 100.0, not 200.0
    assert abs(tick.session_change_pct - 50.0) < 0.01


@pytest.mark.asyncio
async def test_remove_nonexistent_ticker_is_safe():
    """Removing a ticker that doesn't exist should not raise."""
    cache = PriceCache()
    await cache.remove("NONEXISTENT")  # should not raise
