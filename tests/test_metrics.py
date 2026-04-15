"""
tests/test_metrics.py — Unit tests for backtest.metrics.calculate_metrics.
"""

import unittest
from datetime import datetime, timezone, timedelta

from backtest.engine import BacktestResult, Trade
from backtest.metrics import calculate_metrics


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ts(offset_days: float = 0.0):
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return base + timedelta(days=offset_days)


def _trade(net_pnl: float, entry_day: float = 0.0, hold_bars: int = 4,
           commission: float = 0.5) -> Trade:
    entry = _ts(entry_day)
    exit_ = _ts(entry_day + 0.25)  # 6-hour hold
    gross = net_pnl + commission
    return Trade(
        symbol="BTC/USDT", side="long",
        entry_time=entry, exit_time=exit_,
        entry_price=40_000.0, exit_price=40_000.0 * (1 + gross / 3_300),
        size=0.0825,
        gross_pnl=gross, commission=commission, net_pnl=net_pnl,
        reason="signal", hold_bars=hold_bars,
    )


def _result(trades, days: int = 90, initial: float = 3_300.0) -> BacktestResult:
    total_net = sum(t.net_pnl for t in trades)
    eq_start  = _ts(0)
    eq_end    = _ts(days)
    equity_curve = [
        (eq_start + timedelta(days=i), initial + total_net * i / days)
        for i in range(days + 1)
    ]
    return BacktestResult(
        strategy_name="TEST", symbol="BTC/USDT", timeframe="1h",
        start_date=str(eq_start.date()), end_date=str(eq_end.date()),
        initial_balance=initial,
        final_balance=initial + total_net,
        trades=trades,
        equity_curve=equity_curve,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestMetricsNoTrades(unittest.TestCase):

    def test_empty_returns_zero_metrics(self):
        result = _result(trades=[])
        m = calculate_metrics(result)
        self.assertEqual(m["total_trades"], 0)
        self.assertEqual(m["total_return_pct"], 0.0)
        self.assertEqual(m["win_rate_pct"], 0.0)

    def test_metrics_stored_on_result(self):
        result = _result(trades=[])
        calculate_metrics(result)
        self.assertIsInstance(result.metrics, dict)


class TestMetricsWithTrades(unittest.TestCase):

    def setUp(self):
        # 6 wins (+50 each), 4 losses (-30 each) → net +180
        self.trades = (
            [_trade(net_pnl=50.0, entry_day=i) for i in range(6)] +
            [_trade(net_pnl=-30.0, entry_day=i+6) for i in range(4)]
        )
        self.result = _result(self.trades)
        self.m = calculate_metrics(self.result)

    def test_total_trades(self):
        self.assertEqual(self.m["total_trades"], 10)

    def test_winning_losing_counts(self):
        self.assertEqual(self.m["winning_trades"], 6)
        self.assertEqual(self.m["losing_trades"], 4)

    def test_win_rate(self):
        self.assertAlmostEqual(self.m["win_rate_pct"], 60.0, places=1)

    def test_total_return_positive(self):
        self.assertGreater(self.m["total_return_pct"], 0)

    def test_profit_factor_greater_than_one(self):
        self.assertGreater(self.m["profit_factor"], 1.0)

    def test_max_drawdown_non_negative(self):
        self.assertGreaterEqual(self.m["max_drawdown_pct"], 0.0)

    def test_sharpe_calculated(self):
        self.assertIsInstance(self.m["sharpe"], float)

    def test_sortino_calculated(self):
        self.assertIsInstance(self.m["sortino"], float)

    def test_avg_hold_time_is_string(self):
        self.assertIsInstance(self.m["avg_hold_time"], str)

    def test_total_commission_positive(self):
        self.assertGreater(self.m["total_commission"], 0)

    def test_final_balance_matches_result(self):
        self.assertAlmostEqual(self.m["final_balance"], self.result.final_balance, places=2)


class TestMetricsAllLosses(unittest.TestCase):

    def test_profit_factor_zero_with_all_losses(self):
        trades = [_trade(net_pnl=-20.0, entry_day=i) for i in range(5)]
        result = _result(trades)
        m = calculate_metrics(result)
        self.assertEqual(m["winning_trades"], 0)
        self.assertAlmostEqual(m["profit_factor"], 0.0, places=3)

    def test_negative_return(self):
        trades = [_trade(net_pnl=-50.0, entry_day=i) for i in range(5)]
        result = _result(trades)
        m = calculate_metrics(result)
        self.assertLess(m["total_return_pct"], 0)


class TestMetricsDrawdown(unittest.TestCase):

    def test_drawdown_zero_on_monotone_equity(self):
        """Equity that only goes up should have ~0 drawdown."""
        initial = 10_000.0
        equity_curve = [(_ts(i), initial + i * 10) for i in range(90)]
        result = BacktestResult(
            strategy_name="TEST", symbol="BTC/USDT", timeframe="1h",
            start_date="2024-01-01", end_date="2024-04-01",
            initial_balance=initial, final_balance=initial + 890,
            trades=[_trade(10.0, entry_day=i) for i in range(89)],
            equity_curve=equity_curve,
        )
        m = calculate_metrics(result)
        self.assertAlmostEqual(m["max_drawdown_pct"], 0.0, places=0)

    def test_drawdown_detected_on_drop(self):
        """Equity that rises then falls should have positive drawdown."""
        initial = 10_000.0
        curve = (
            [(_ts(i), initial + i * 50) for i in range(45)] +
            [(_ts(45 + i), initial + 45 * 50 - i * 60) for i in range(45)]
        )
        trades = [_trade(10.0, entry_day=i) for i in range(10)]
        result = BacktestResult(
            strategy_name="TEST", symbol="BTC/USDT", timeframe="1h",
            start_date="2024-01-01", end_date="2024-04-01",
            initial_balance=initial,
            final_balance=float(curve[-1][1]),
            trades=trades,
            equity_curve=curve,
        )
        m = calculate_metrics(result)
        self.assertGreater(m["max_drawdown_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()
