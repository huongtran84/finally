from datetime import datetime, timezone

from app.market_data.models import PriceTick, TickerConfig, PriceSnapshot, PriceHistory


def test_price_tick_direction_up():
    assert PriceTick.compute_direction(100.0, 99.0) == "up"


def test_price_tick_direction_down():
    assert PriceTick.compute_direction(99.0, 100.0) == "down"


def test_price_tick_direction_flat():
    assert PriceTick.compute_direction(100.0, 100.0) == "flat"


def test_price_tick_construction():
    now = datetime.now(timezone.utc)
    tick = PriceTick(
        ticker="AAPL",
        price=192.0,
        previous_price=191.0,
        timestamp=now,
        direction="up",
        session_change_pct=1.5,
    )
    assert tick.ticker == "AAPL"
    assert tick.price == 192.0
    assert tick.direction == "up"


def test_ticker_config_defaults():
    cfg = TickerConfig(ticker="TEST", seed_price=100.0)
    assert cfg.drift == 0.0
    assert cfg.volatility == 0.3
    assert cfg.sector == "tech"


def test_price_history_construction():
    now = datetime.now(timezone.utc)
    snapshot = PriceSnapshot(price=100.0, timestamp=now)
    history = PriceHistory(
        ticker="AAPL",
        prices=[snapshot],
        session_start_price=100.0,
    )
    assert history.ticker == "AAPL"
    assert len(history.prices) == 1
    assert history.session_start_price == 100.0
