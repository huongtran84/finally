"""Data models for the market data layer."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PriceUpdate:
    """A single price update for one ticker."""

    ticker: str
    price: float              # Current price (VND)
    previous_price: float     # Price from the previous update
    open: float               # Session/day open price
    high: float               # Session/day high
    low: float                # Session/day low
    volume: int               # Trading volume (shares)
    timestamp: str            # ISO 8601
    basic_price: float        # Reference price (gia tham chieu)
    ceiling_price: float      # Gia tran
    floor_price: float        # Gia san
    exchange: str             # "HOSE", "HNX", "UPCOM"

    @property
    def day_change_pct(self) -> float:
        """Percentage change from the day's open price.

        Note: this is *not* the same as the session change % shown in the UI.
        The UI's "session change %" (change since backend started) is computed
        in :meth:`to_sse_dict` using the ``session_open`` argument supplied by
        the SSE layer from ``PriceCache.get_session_open()``.
        """
        if self.open == 0:
            return 0.0
        return round((self.price - self.open) / self.open * 100, 2)

    @property
    def is_ceiling(self) -> bool:
        """True if price has hit the ceiling (gia tran)."""
        return self.price >= self.ceiling_price

    @property
    def is_floor(self) -> bool:
        """True if price has hit the floor (gia san)."""
        return self.price <= self.floor_price

    def to_sse_dict(self, session_open: float) -> dict:
        """Serialize to a dict suitable for SSE event payload."""
        session_change = 0.0
        if session_open > 0:
            session_change = round(
                (self.price - session_open) / session_open * 100, 2
            )
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previousPrice": self.previous_price,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "volume": self.volume,
            "timestamp": self.timestamp,
            "basicPrice": self.basic_price,
            "ceilingPrice": self.ceiling_price,
            "floorPrice": self.floor_price,
            "exchange": self.exchange,
            "sessionChangePct": session_change,
            "isCeiling": self.is_ceiling,
            "isFloor": self.is_floor,
        }


@dataclass
class StockConfig:
    """Configuration for a single stock in the simulator."""

    ticker: str
    name: str
    exchange: str        # "HOSE", "HNX", "UPCOM"
    sector: str          # Used for sector correlation grouping
    initial_price: float # Starting price in VND
    mu: float            # Annualized drift
    sigma: float         # Annualized volatility


@dataclass
class TickerState:
    """Internal simulator state for a single ticker."""

    config: StockConfig
    current_price: float
    previous_price: float
    session_open: float      # Price when backend started (for session_change_pct)
    basic_price: float       # Reference price (= session_open for simulator)
    day_open: float
    day_high: float
    day_low: float
    day_volume: int = 0


# ---------------------------------------------------------------------------
# Default Vietnamese stock configurations
# ---------------------------------------------------------------------------

DEFAULT_STOCKS: list[StockConfig] = [
    StockConfig("VNM", "Vinamilk",           "HOSE", "consumer",    75_000,  0.05, 0.25),
    StockConfig("VCB", "Vietcombank",         "HOSE", "banking",     92_000,  0.08, 0.30),
    StockConfig("VIC", "Vingroup",            "HOSE", "real_estate", 42_000,  0.03, 0.35),
    StockConfig("HPG", "Hoa Phat Group",      "HOSE", "materials",   28_000,  0.06, 0.40),
    StockConfig("FPT", "FPT Corporation",     "HOSE", "technology",  130_000, 0.10, 0.30),
    StockConfig("MWG", "The Gioi Di Dong",    "HOSE", "retail",      45_000,  0.04, 0.35),
    StockConfig("TCB", "Techcombank",         "HOSE", "banking",     25_000,  0.07, 0.30),
    StockConfig("VHM", "Vinhomes",            "HOSE", "real_estate", 38_000,  0.04, 0.35),
    StockConfig("GAS", "PetroVietnam Gas",    "HOSE", "energy",      85_000,  0.05, 0.25),
    StockConfig("MSN", "Masan Group",         "HOSE", "consumer",    68_000,  0.06, 0.30),
]

DEFAULT_STOCKS_MAP: dict[str, StockConfig] = {s.ticker: s for s in DEFAULT_STOCKS}

# ---------------------------------------------------------------------------
# Price limit rules by exchange
# ---------------------------------------------------------------------------

PRICE_LIMITS: dict[str, float] = {
    "HOSE":  0.07,   # ±7%
    "HNX":   0.10,   # ±10%
    "UPCOM": 0.15,   # ±15%
}
