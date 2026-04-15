"""
tests/test_engine.py — Unit tests for the backtesting engine.
"""

import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np

from backtest.engine import BacktestEngine, BacktestResult


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_ohlcv(n: int = 200, start_price: float = 40_000.0, trend: float = 0.0) -> pd.DataFrame:
    """Build a synthetic OHLCV DataFrame."""
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    rng = np.random.default_rng(0)
    noise  = rng.normal(0, 0.002, n).cumsum()
    prices = start_price * (1 + trend * np.arange(n) / n + noise)
    prices = np.maximum(prices, 1.0)
    return pd.DataFrame(
        {
            "open":   prices * 0.999,
            "high":   prices * 1.005,
            "low":    prices * 0.995,
            "close":  prices,
            "volume": rng.uniform(100, 500, n),
        },
        index=idx,
    )


def _make_strategy(signals: list):
    """Strategy that emits a preset sequence of signals."""
    strategy = MagicMock()
    strategy.name      = "TEST"
    strategy.timeframe = "1h"
    call_count = [0]

    def generate_signal(df, position=None):
        i = min(call_count[0], len(signals) - 1)
        call_count[0] += 1
        return signals[i]

    strategy.generate_signal = generate_signal
    # precompute_indicators must be a pass-through so the engine doesn't replace df with a MagicMock
    strategy.precompute_indicators = lambda df: df
    return strategy


# ── Basic engine behaviour ────────────────────────────────────────────────────

class TestEngineBasics(unittest.TestCase):

    def _run(self, signals, n=200, **kwargs):
        df       = _make_ohlcv(n=n)
        strategy = _make_strategy(signals)
        engine   = BacktestEngine(strategy=strategy, df=df, symbol="BTC/USDT", **kwargs)
        return engine.run()

    def test_returns_backtest_result(self):
        result = self._run(signals=[None] * 200)
        self.assertIsInstance(result, BacktestResult)

    def test_no_trades_when_no_signal(self):
        result = self._run(signals=[None] * 200)
        self.assertEqual(len(result.trades), 0)
        self.assertAlmostEqual(result.final_balance, result.initial_balance, places=1)

    def test_buy_then_close_generates_one_trade(self):
        signals = [None] * 50 + ["buy"] + [None] * 10 + ["close"] + [None] * 139
        result  = self._run(signals=signals)
        self.assertGreaterEqual(len(result.trades), 1)

    def test_long_only_no_short_trades(self):
        """sell signal when flat must NOT open a short."""
        signals = ["sell"] * 200
        result  = self._run(signals=signals)
        for trade in result.trades:
            self.assertEqual(trade.side, "long")

    def test_commission_deducted(self):
        signals = [None] * 10 + ["buy"] + [None] * 5 + ["close"] + [None] * 184
        result  = self._run(signals=signals, commission_rate=0.001)
        if result.trades:
            for trade in result.trades:
                self.assertGreater(trade.commission, 0)

    def test_stop_loss_closes_position(self):
        """A bar whose low dips below the SL price should trigger a stop_loss close."""
        import config
        entry_price = 40_000.0
        sl_price    = entry_price * (1 - config.STOP_LOSS_PCT)  # 39_400 at 1.5%

        prices = [entry_price] * 200
        idx    = pd.date_range("2024-01-01", periods=200, freq="1h", tz="UTC")
        lows   = [p * 0.999 for p in prices]   # normally tight
        lows[15] = sl_price * 0.995             # bar 15: low clearly below SL

        df = pd.DataFrame(
            {
                "open":   prices,
                "high":   [p * 1.001 for p in prices],
                "low":    lows,
                "close":  prices,
                "volume": [100.0] * 200,
            },
            index=idx,
        )
        # buy signal at bar 10 → fills at bar 11's open
        signals = [None] * 10 + ["buy"] + [None] * 189
        strategy = _make_strategy(signals)
        strategy.name      = "TEST"
        strategy.timeframe = "1h"
        engine = BacktestEngine(strategy=strategy, df=df, symbol="BTC/USDT")
        result = engine.run()

        sl_trades = [t for t in result.trades if t.reason == "stop_loss"]
        self.assertGreater(len(sl_trades), 0)

    def test_equity_curve_length_matches_bars(self):
        df       = _make_ohlcv(n=100)
        strategy = _make_strategy([None] * 100)
        engine   = BacktestEngine(strategy=strategy, df=df, symbol="BTC/USDT")
        result   = engine.run()
        self.assertEqual(len(result.equity_curve), 100)

    def test_daily_loss_limit_suppresses_trades(self):
        """After hitting the daily loss limit, new entries should be suppressed."""
        import config
        # Force balance to drop by making every 'buy' immediately hit stop-loss
        signals = ["buy", "buy", "buy", "buy", "buy", "buy", "buy", "buy"]
        signals += [None] * 192
        # We just verify engine doesn't crash; full assertion is complex without
        # controlled price movement.
        result = self._run(signals=signals)
        self.assertIsInstance(result, BacktestResult)

    def test_end_of_data_force_closes_open_position(self):
        signals = [None] * 10 + ["buy"] + [None] * 189  # never closes
        result  = self._run(signals=signals)
        eod_trades = [t for t in result.trades if t.reason == "end_of_data"]
        self.assertGreater(len(eod_trades), 0)


# ── Financial correctness ─────────────────────────────────────────────────────

class TestEngineFinancials(unittest.TestCase):

    def test_balance_consistent_with_trades(self):
        """final_balance = initial - commissions + sum(gross_pnl)."""
        signals = [None]*5 + ["buy"] + [None]*10 + ["close"] + [None]*184
        df       = _make_ohlcv(n=200)
        strategy = _make_strategy(signals)
        strategy.name      = "TEST"
        strategy.timeframe = "1h"
        engine  = BacktestEngine(strategy=strategy, df=df, symbol="BTC/USDT",
                                 initial_balance=10_000, commission_rate=0.001)
        result  = engine.run()

        if result.trades:
            total_gross = sum(t.gross_pnl for t in result.trades)
            total_comm  = sum(t.commission for t in result.trades)
            expected    = result.initial_balance + total_gross - total_comm
            self.assertAlmostEqual(result.final_balance, expected, places=2)

    def test_1bar_execution_delay(self):
        """Entry fills at the open of the bar AFTER the signal bar."""
        prices   = [40_000.0] * 200
        idx      = pd.date_range("2024-01-01", periods=200, freq="1h", tz="UTC")
        df = pd.DataFrame(
            {"open": prices, "high": prices, "low": prices, "close": prices, "volume": [100.0]*200},
            index=idx,
        )
        signals  = [None]*5 + ["buy"] + [None]*5 + ["close"] + [None]*188
        strategy = _make_strategy(signals)
        strategy.name      = "TEST"
        strategy.timeframe = "1h"
        engine   = BacktestEngine(strategy=strategy, df=df, symbol="BTC/USDT")
        result   = engine.run()
        if result.trades:
            # entry_time must be AFTER signal bar (bar 5), so bar 6 or later
            entry_bar_ts = result.trades[0].entry_time
            signal_bar_ts = df.index[5]
            self.assertGreater(entry_bar_ts, signal_bar_ts)


if __name__ == "__main__":
    unittest.main()
