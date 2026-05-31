"""
Global configuration for the paper trading bot system.
Values can be overridden via a .env file (see .env.example).
"""

import os
from dotenv import load_dotenv

load_dotenv()  # loads .env if present; silently skips if missing

def _float(key: str, default: float) -> float:
    return float(os.getenv(key, default))

def _int(key: str, default: int) -> int:
    return int(os.getenv(key, default))

# ── Safety ────────────────────────────────────────────────────────────────────
PAPER_TRADING = True  # Must always be True; guards against accidental live trading

# ── Capital allocation ────────────────────────────────────────────────────────
INITIAL_BALANCE = _float("INITIAL_BALANCE", 1_000)   # USDT, split across the three bots

BOT_ALLOCATIONS = {
    "MACD":     0.33,
    "RSI_VWAP": 0.33,
    "CVD":      0.34,
}

# ── Universe ──────────────────────────────────────────────────────────────────
SYMBOLS = ["BTC/USDT", "SOL/USDT", "RENDER/USDT"]

# ── Risk parameters ───────────────────────────────────────────────────────────
MAX_POSITION_PCT    = _float("MAX_POSITION_PCT",   0.10)  # 10% of bot balance per trade
STOP_LOSS_PCT       = _float("STOP_LOSS_PCT",      0.015) # 1.5%
TAKE_PROFIT_PCT     = _float("TAKE_PROFIT_PCT",    0.030) # 3.0%
MAX_DAILY_LOSS_PCT  = _float("MAX_DAILY_LOSS_PCT", 0.05)  # 5% — bot pauses if breached

# ── Timeframes ────────────────────────────────────────────────────────────────
TIMEFRAMES = {
    "MACD":     "5m",
    "RSI_VWAP": "1h",
    "CVD":      "15m",
}

# ── Exchange ──────────────────────────────────────────────────────────────────
EXCHANGE_ID     = os.getenv("EXCHANGE_ID", "bybit")
EXCHANGE_OPTS   = {"defaultType": "spot"}  # Spot market (no leverage)
COMMISSION_RATE = _float("COMMISSION_RATE", 0.001)  # 0.1% per trade side (Bybit spot taker)


def make_exchange():
    """Return a configured ccxt exchange instance (spot by default)."""
    import ccxt
    exchange_cls = getattr(ccxt, EXCHANGE_ID)
    ex = exchange_cls(EXCHANGE_OPTS)
    ex.load_markets()
    return ex

# ── Candle history ────────────────────────────────────────────────────────────
OHLCV_LIMIT  = 200   # candles fetched by ccxt for one-off REST calls
WARMUP_BARS  = 250   # candles seeded into each KlineBuffer at startup

# ── Risk guard loop ───────────────────────────────────────────────────────────
SL_TP_CHECK_SECS = _int("SL_TP_CHECK_SECS", 5)  # background SL/TP poll interval

# ── Web dashboard ─────────────────────────────────────────────────────────────
DASHBOARD_API_KEY = os.getenv("DASHBOARD_API_KEY", "")  # empty = auth disabled (local dev)

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_DIR        = "logs"
TRADE_LOG_FILE = "logs/trades.csv"
APP_LOG_FILE   = "logs/trading.log"

# ── Terminal dashboard ────────────────────────────────────────────────────────
DASHBOARD_REFRESH_SECS = 10
