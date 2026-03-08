from pydantic import BaseModel
from datetime import datetime


class PriceTick(BaseModel):
    """A single price update for one ticker."""
    ticker: str
    price: float
    previous_price: float
    timestamp: datetime
    direction: str  # "up", "down", or "flat"
    session_change_pct: float  # % change from session start price

    @staticmethod
    def compute_direction(price: float, previous_price: float) -> str:
        if price > previous_price:
            return "up"
        elif price < previous_price:
            return "down"
        return "flat"


class TickerConfig(BaseModel):
    """Configuration for a single simulated ticker."""
    ticker: str
    seed_price: float
    drift: float = 0.0       # annualized drift (mu)
    volatility: float = 0.3  # annualized volatility (sigma)
    sector: str = "tech"


class PriceSnapshot(BaseModel):
    """A point-in-time price for history."""
    price: float
    timestamp: datetime


class PriceHistory(BaseModel):
    """Rolling price history for one ticker."""
    ticker: str
    prices: list[PriceSnapshot]
    session_start_price: float
