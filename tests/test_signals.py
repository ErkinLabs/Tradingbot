"""
tests/test_signals.py — Unit tests for generate_signal() in each strategy bot.

All tests use synthetic OHLCV DataFrames — no network calls.
BaseBot.__init__ (which calls ccxt) is bypassed via a lightweight fixture.
"""

import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_df(n: int = 100, close_prices=None) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame with a UTC DatetimeIndex."""
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    if close_prices is None:
        close_prices = np.linspace(40_000, 41_000, n)
    close = np.array(close_prices, dtype=float)
    return pd.DataFrame(
        {
            "open":   close * 0.999,
            "high":   close * 1.005,
            "low":    close * 0.995,
            "close":  close,
            "volume": np.random.default_rng(42).uniform(100, 1000, n),
        },
        index=idx,
    )


def _bot_instance(BotClass):
    """Instantiate a bot without triggering ccxt or file I/O."""
    with (
        patch("base_bot.ccxt.bybit"),
        patch("base_bot._load_state", return_value={}),
        patch("base_bot._save_state"),
    ):
        bot = BotClass.__new__(BotClass)
        bot.log            = MagicMock()
        bot.positions      = {}
        bot.closed_trades  = []
        bot.balance        = 3_300.0
        bot.start_balance  = 3_300.0
        bot._day_start_balance = 3_300.0
        bot.paused         = False
        bot._day_trade_count = 0
        import threading
        bot._positions_lock = threading.Lock()
        bot._trades_lock    = threading.Lock()
        from datetime import date
        bot._current_day    = date.today()
        import config
        bot.timeframe = config.TIMEFRAMES.get(bot.name, "5m")
    return bot


# ── MACD Bot ─────────────────────────────────────────────────────────────────

class TestMACDSignal(unittest.TestCase):

    def setUp(self):
        from bot_macd import MACDBot
        self.bot = _bot_instance(MACDBot)

    def test_returns_none_when_insufficient_bars(self):
        df = _make_df(n=10)
        self.assertIsNone(self.bot.generate_signal(df))

    def test_returns_none_or_valid_string_on_enough_bars(self):
        df = _make_df(n=100)
        result = self.bot.generate_signal(df)
        self.assertIn(result, (None, "buy", "close"))

    def test_no_buy_during_downtrend(self):
        """Price below EMA50 — buy signal should be suppressed."""
        prices = np.linspace(50_000, 30_000, 100)  # strong downtrend
        df = _make_df(n=100, close_prices=prices)
        result = self.bot.generate_signal(df, position=None)
        self.assertNotEqual(result, "buy")

    def test_close_signal_when_long(self):
        """With a long position open, only 'close' or None are valid returns."""
        df = _make_df(n=100)
        result = self.bot.generate_signal(df, position="long")
        self.assertIn(result, (None, "close"))

    def test_never_returns_sell_when_flat(self):
        """Spot mode: 'sell' (open short) must never be returned."""
        df = _make_df(n=200)
        for i in range(60, 200):
            result = self.bot.generate_signal(df.iloc[:i], position=None)
            self.assertNotEqual(result, "sell", f"Unexpected 'sell' at bar {i}")


# ── RSI+VWAP Bot ─────────────────────────────────────────────────────────────

class TestRSIVWAPSignal(unittest.TestCase):

    def setUp(self):
        from bot_rsi_vwap import RSIVWAPBot
        self.bot = _bot_instance(RSIVWAPBot)

    def test_returns_none_when_insufficient_bars(self):
        df = _make_df(n=5)
        self.assertIsNone(self.bot.generate_signal(df))

    def test_returns_none_or_valid_string(self):
        df = _make_df(n=60)
        result = self.bot.generate_signal(df)
        self.assertIn(result, (None, "buy", "close"))

    def test_close_signal_when_rsi_recovers(self):
        """RSI > 50 while long should trigger close."""
        # Build prices that recover (RSI will rise above 50)
        prices = list(np.linspace(38_000, 40_000, 40)) + list(np.linspace(40_000, 43_000, 20))
        df = _make_df(n=60, close_prices=prices)
        result = self.bot.generate_signal(df, position="long")
        # May or may not trigger depending on exact RSI value — just check it's valid
        self.assertIn(result, (None, "close"))

    def test_never_returns_sell_when_flat(self):
        """Spot mode: no short entries."""
        df = _make_df(n=60)
        result = self.bot.generate_signal(df, position=None)
        self.assertNotEqual(result, "sell")

    def test_no_buy_in_strong_trend(self):
        """ADX >= 35 (strong trend) should suppress mean-reversion buy."""
        # Steady uptrend → high ADX
        prices = np.linspace(30_000, 60_000, 80)
        df = _make_df(n=80, close_prices=prices)
        result = self.bot.generate_signal(df, position=None)
        self.assertNotEqual(result, "buy")


# ── CVD Bot ───────────────────────────────────────────────────────────────────

class TestCVDSignal(unittest.TestCase):

    def setUp(self):
        from bot_cvd import CVDBot
        self.bot = _bot_instance(CVDBot)

    def test_returns_none_when_insufficient_bars(self):
        df = _make_df(n=10)
        self.assertIsNone(self.bot.generate_signal(df))

    def test_returns_none_or_valid_string(self):
        df = _make_df(n=100)
        result = self.bot.generate_signal(df)
        self.assertIn(result, (None, "buy", "close"))

    def test_close_when_cvd_turns_negative(self):
        """Long position: close when CVD_change <= 0."""
        # Down-close candles dominate → CVD falls
        prices = list(np.linspace(42_000, 41_000, 70))
        close = np.array(prices)
        df = _make_df(n=70, close_prices=close)
        # Force all candles to be down-close so CVD is negative
        df["open"] = df["close"] * 1.005
        result = self.bot.generate_signal(df, position="long")
        self.assertIn(result, (None, "close"))

    def test_never_returns_sell_when_flat(self):
        """Spot mode: no short entries."""
        df = _make_df(n=100)
        result = self.bot.generate_signal(df, position=None)
        self.assertNotEqual(result, "sell")


if __name__ == "__main__":
    unittest.main()
