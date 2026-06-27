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
INITIAL_BALANCE = _float("INITIAL_BALANCE", 10_000)  # USDT, split across the three bots

BOT_ALLOCATIONS = {
    "MACD":     0.33,
    "RSI_VWAP": 0.33,
    "CVD":      0.34,
}

# ── Universe ──────────────────────────────────────────────────────────────────
SYMBOLS = ["BTC/USDT", "SOL/USDT"]

# ── Risk parameters ───────────────────────────────────────────────────────────
MAX_POSITION_PCT    = _float("MAX_POSITION_PCT",   0.10)  # 10% of bot balance per trade
STOP_LOSS_PCT       = _float("STOP_LOSS_PCT",      0.015) # 1.5%
TAKE_PROFIT_PCT     = _float("TAKE_PROFIT_PCT",    0.030) # 3.0%
MAX_DAILY_LOSS_PCT  = _float("MAX_DAILY_LOSS_PCT", 0.05)  # 5% — bot pauses if breached
MAX_DAILY_TRADES    = _int("MAX_DAILY_TRADES",     5)     # max new entries per day per bot

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

# ── Candle history ────────────────────────────────────────────────────────────
OHLCV_LIMIT = 200  # number of candles to fetch per symbol

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_DIR        = "logs"
TRADE_LOG_FILE = "logs/trades.csv"
APP_LOG_FILE   = "logs/trading.log"

# ── Dashboard ─────────────────────────────────────────────────────────────────
DASHBOARD_REFRESH_SECS = 10

# ── Main loop ─────────────────────────────────────────────────────────────────
BOT_LOOP_SECS = 60  # each bot calls run_once() every N seconds
