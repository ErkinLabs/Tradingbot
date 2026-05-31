"""
KlineBuffer — thread-safe rolling OHLCV DataFrame backed by WebSocket kline messages.

Each buffer tracks one (symbol, timeframe) pair.
"""

import threading
from collections import deque
from typing import Optional

import pandas as pd


class KlineBuffer:
    """
    Rolling window of OHLCV bars updated from Bybit WebSocket kline messages.

    Lifecycle:
        buf = KlineBuffer(maxlen=250)
        buf.seed(df)               # called once at startup from ccxt REST
        confirmed = buf.update(c)  # called on every WebSocket tick
        df = buf.get_df()          # DataFrame snapshot for generate_signal()
        price = buf.last_price     # most recent close, for SL/TP checks
    """

    def __init__(self, maxlen: int = 250) -> None:
        self._lock       = threading.Lock()
        self._rows: deque = deque(maxlen=maxlen)
        self._last_price: Optional[float] = None

    # ── Startup warmup ────────────────────────────────────────────────────────

    def seed(self, df: pd.DataFrame) -> None:
        """
        Populate buffer from historical OHLCV DataFrame (ccxt format).
        Index must be a DatetimeIndex. Called once before WebSocket starts.
        """
        with self._lock:
            self._rows.clear()
            for ts, row in df.iterrows():
                self._rows.append({
                    "timestamp": ts,
                    "open":      float(row["open"]),
                    "high":      float(row["high"]),
                    "low":       float(row["low"]),
                    "close":     float(row["close"]),
                    "volume":    float(row["volume"]),
                })
            if self._rows:
                self._last_price = self._rows[-1]["close"]

    # ── WebSocket update ──────────────────────────────────────────────────────

    def update(self, candle: dict) -> bool:
        """
        Ingest one kline entry from a Bybit WebSocket message.

        Bybit sends both in-progress ticks (confirm=false) and the final
        closed-bar tick (confirm=true). In-progress ticks overwrite the last
        row in place; the confirmed tick appends a new row.

        Parameters
        ----------
        candle : dict with keys: start, open, high, low, close, volume, confirm

        Returns
        -------
        True if the candle is confirmed (closed), False for in-progress ticks.
        """
        ts = pd.Timestamp(int(candle["start"]), unit="ms", tz="UTC")
        row = {
            "timestamp": ts,
            "open":      float(candle["open"]),
            "high":      float(candle["high"]),
            "low":       float(candle["low"]),
            "close":     float(candle["close"]),
            "volume":    float(candle["volume"]),
        }
        confirmed = bool(candle.get("confirm", False))

        with self._lock:
            self._last_price = row["close"]
            if self._rows and self._rows[-1]["timestamp"] == ts:
                # Same candle still open — overwrite last row
                self._rows[-1] = row
            else:
                # New bar (or first message)
                self._rows.append(row)

        return confirmed

    # ── Data access ───────────────────────────────────────────────────────────

    def get_df(self) -> pd.DataFrame:
        """
        Return a snapshot DataFrame with DatetimeIndex, suitable for generate_signal().
        Columns: open, high, low, close, volume (all float).
        """
        with self._lock:
            rows = list(self._rows)
        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame(rows).set_index("timestamp")
        return df.astype(float)

    @property
    def last_price(self) -> Optional[float]:
        """Most recently seen close price. Thread-safe. None until first update."""
        return self._last_price
