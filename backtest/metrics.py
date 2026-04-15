"""
metrics.py — Performance metrics calculator for BacktestResult.

All metrics are calculated from Trade list + equity curve.
"""

from __future__ import annotations

import math
from datetime import timedelta
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from .engine import BacktestResult

_TRADING_DAYS = 252.0
_RISK_FREE_DEFAULT = 0.02  # annual


def calculate_metrics(result: "BacktestResult", risk_free_rate: float = _RISK_FREE_DEFAULT) -> dict:
    """
    Compute all performance metrics and return them as a flat dict.
    Stores the result in result.metrics and also returns it.
    """
    m = _compute(result, risk_free_rate)
    result.metrics = m
    return m


# ── Core computation ───────────────────────────────────────────────────────────

def _compute(result: "BacktestResult", rfr: float) -> dict:
    trades       = result.trades
    equity_curve = result.equity_curve
    n_trades     = len(trades)

    if n_trades == 0 or len(equity_curve) < 2:
        return _empty_metrics()

    winning = [t for t in trades if t.net_pnl > 0]
    losing  = [t for t in trades if t.net_pnl < 0]

    # ── Returns ───────────────────────────────────────────────────────────────
    initial = result.initial_balance
    final   = result.final_balance
    total_return_pct = (final - initial) / initial * 100

    start_ts = equity_curve[0][0]
    end_ts   = equity_curve[-1][0]
    days     = (end_ts - start_ts).total_seconds() / 86_400
    years    = max(days / 365.25, 1 / 365.25)

    annual_return_pct = ((1 + total_return_pct / 100) ** (1 / years) - 1) * 100

    # ── Drawdown ──────────────────────────────────────────────────────────────
    max_dd_pct, max_dd_days = _max_drawdown(equity_curve)

    # ── Sharpe & Sortino ──────────────────────────────────────────────────────
    sharpe  = _sharpe(equity_curve, rfr)
    sortino = _sortino(equity_curve, rfr)

    # ── Calmar ────────────────────────────────────────────────────────────────
    calmar = annual_return_pct / abs(max_dd_pct) if max_dd_pct != 0 else float("inf")

    # ── Win / loss stats ──────────────────────────────────────────────────────
    win_rate = len(winning) / n_trades * 100

    gross_profit = sum(t.net_pnl for t in winning)
    gross_loss   = abs(sum(t.net_pnl for t in losing))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    avg_win     = gross_profit / len(winning) if winning else 0.0
    avg_loss    = sum(t.net_pnl for t in losing) / len(losing) if losing else 0.0
    largest_win  = max((t.net_pnl for t in trades), default=0.0)
    largest_loss = min((t.net_pnl for t in trades), default=0.0)

    # ── Hold time ─────────────────────────────────────────────────────────────
    hold_secs = [
        (t.exit_time - t.entry_time).total_seconds() for t in trades
    ]
    avg_hold = _fmt_duration(sum(hold_secs) / n_trades)

    # ── Commission ────────────────────────────────────────────────────────────
    total_commission = sum(t.commission for t in trades)

    return {
        "total_return_pct":      round(total_return_pct,  2),
        "annual_return_pct":     round(annual_return_pct, 2),
        "max_drawdown_pct":      round(max_dd_pct,        2),
        "max_dd_duration_days":  round(max_dd_days,       1),
        "sharpe":                round(sharpe,            3),
        "sortino":               round(sortino,           3),
        "calmar":                round(calmar,            3),
        "win_rate_pct":          round(win_rate,          2),
        "profit_factor":         round(profit_factor,     3),
        "avg_win":               round(avg_win,           4),
        "avg_loss":              round(avg_loss,          4),
        "largest_win":           round(largest_win,       4),
        "largest_loss":          round(largest_loss,      4),
        "avg_hold_time":         avg_hold,
        "total_trades":          n_trades,
        "winning_trades":        len(winning),
        "losing_trades":         len(losing),
        "total_commission":      round(total_commission,  4),
        "initial_balance":       round(result.initial_balance, 2),
        "final_balance":         round(result.final_balance,   2),
    }


def _empty_metrics() -> dict:
    return {
        "total_return_pct": 0.0, "annual_return_pct": 0.0,
        "max_drawdown_pct": 0.0, "max_dd_duration_days": 0.0,
        "sharpe": 0.0, "sortino": 0.0, "calmar": 0.0,
        "win_rate_pct": 0.0, "profit_factor": 0.0,
        "avg_win": 0.0, "avg_loss": 0.0,
        "largest_win": 0.0, "largest_loss": 0.0,
        "avg_hold_time": "—",
        "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
        "total_commission": 0.0, "initial_balance": 0.0, "final_balance": 0.0,
    }


# ── Helper functions ───────────────────────────────────────────────────────────

def _daily_returns(equity_curve: list[tuple]) -> pd.Series:
    """Resample equity curve to daily close and compute % returns."""
    series = pd.Series(
        {ts: eq for ts, eq in equity_curve},
        dtype=float,
    )
    series.index = pd.to_datetime(series.index, utc=True)
    daily = series.resample("D").last().dropna()
    return daily.pct_change().dropna()


def _sharpe(equity_curve: list[tuple], rfr: float) -> float:
    dr = _daily_returns(equity_curve)
    if len(dr) < 2 or dr.std() == 0:
        return 0.0
    daily_rf       = rfr / 365
    excess_returns = dr - daily_rf
    return float(excess_returns.mean() / dr.std() * math.sqrt(_TRADING_DAYS))


def _sortino(equity_curve: list[tuple], rfr: float) -> float:
    dr = _daily_returns(equity_curve)
    if len(dr) < 2:
        return 0.0
    daily_rf       = rfr / 365
    excess_returns = dr - daily_rf
    downside       = dr[dr < daily_rf]
    if len(downside) == 0:
        return float("inf") if excess_returns.mean() > 0 else 0.0
    downside_std = float(
        math.sqrt((downside ** 2).mean()) * math.sqrt(_TRADING_DAYS)
    )
    if downside_std == 0:
        return 0.0
    return float(excess_returns.mean() * _TRADING_DAYS / downside_std)


def _max_drawdown(equity_curve: list[tuple]) -> tuple[float, float]:
    """Return (max_drawdown_pct, duration_in_days)."""
    peak_val = equity_curve[0][1]
    peak_ts  = equity_curve[0][0]
    max_dd   = 0.0
    max_dd_days = 0.0

    for ts, eq in equity_curve:
        if eq > peak_val:
            peak_val = eq
            peak_ts  = ts
        dd = (peak_val - eq) / peak_val * 100 if peak_val > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
            duration = (ts - peak_ts).total_seconds() / 86_400
            max_dd_days = max(max_dd_days, duration)

    return max_dd, max_dd_days


def _fmt_duration(seconds: float) -> str:
    """Format seconds as '4h 23m' or '2d 1h 5m'."""
    seconds = int(seconds)
    d, rem  = divmod(seconds, 86_400)
    h, rem  = divmod(rem,     3_600)
    m, _    = divmod(rem,     60)
    parts   = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    parts.append(f"{m}m")
    return " ".join(parts) if parts else "0m"


# ── Daily returns helper (used by report.py for correlation) ──────────────────

def daily_equity_returns(equity_curve: list[tuple]) -> pd.Series:
    """Exported so report.py can call it for correlation matrix."""
    return _daily_returns(equity_curve)
