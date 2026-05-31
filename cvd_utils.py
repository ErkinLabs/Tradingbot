"""
CVD (Cumulative Volume Delta) calculation utilities.

Two methods:
  - bar_direction : proxy used in backtest (up-bar → +volume, down-bar → −volume)
  - trade_delta   : signed volume from individual trades (buy − sell)
"""

from __future__ import annotations

from typing import Iterable

import pandas as pd


def calc_cvd_bar_direction(df: pd.DataFrame) -> pd.Series:
    """
    Bar-direction CVD approximation.
    Up-close bar → +volume; down-close bar → −volume; doji → 0.
    """
    signed = df["volume"].where(
        df["close"] > df["open"],
        -df["volume"].where(df["close"] < df["open"], 0),
    )
    return signed.cumsum()


def calc_cvd_trade_delta(
    bar_index: pd.DatetimeIndex,
    trades: Iterable[dict],
) -> pd.Series:
    """
    Trade-level CVD aligned to OHLCV bar timestamps.

    Each trade dict must have keys: timestamp (ms or datetime), amount, side ('buy'|'sell').
    Volume is signed: buy → +amount, sell → −amount, then cumsum per bar alignment.
    """
    if bar_index.empty:
        return pd.Series(dtype=float)

    deltas = pd.Series(0.0, index=bar_index)
    if not trades:
        return deltas.cumsum()

    for trade in trades:
        ts = trade["timestamp"]
        if isinstance(ts, (int, float)):
            ts = pd.Timestamp(int(ts), unit="ms", tz="UTC")
        else:
            ts = pd.Timestamp(ts).tz_convert("UTC") if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts, tz="UTC")

        amount = float(trade["amount"])
        side   = str(trade.get("side", "")).lower()
        signed = amount if side == "buy" else -amount

        # Assign to the bar that contains this trade
        idx = bar_index.searchsorted(ts, side="right") - 1
        if idx < 0:
            continue
        bar_ts = bar_index[idx]
        deltas.loc[bar_ts] += signed

    return deltas.cumsum()


def cvd_series_diff_pct(bar_cvd: pd.Series, trade_cvd: pd.Series) -> float:
    """Return mean absolute % difference between two CVD series (for parity tests)."""
    aligned = pd.concat([bar_cvd, trade_cvd], axis=1, keys=["bar", "trade"]).dropna()
    if aligned.empty:
        return 0.0
    denom = aligned["trade"].abs().replace(0, pd.NA)
    diff  = ((aligned["bar"] - aligned["trade"]).abs() / denom * 100).dropna()
    return float(diff.mean()) if not diff.empty else 0.0
