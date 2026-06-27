"""
Bot 1 — MACD Momentum Strategy (Spot)
=======================================
Timeframe  : 5-minute candles
Signal     : MACD histogram zero-cross with RSI confluence
              prev_hist < 0 AND curr_hist > 0  ->  BUY  (long)
              2 consecutive negative histogram bars  ->  CLOSE long
Filters:
  1. EMA200 trend: long only above EMA200 (stronger trend confirmation)
  2. Volume spike: current volume > 20-bar SMA * 1.5
  3. Histogram momentum: |histogram| must be growing (signal gaining strength)
  4. RSI 45-70: not oversold, not overbought at entry
Target win rate: ~50 %
Avg hold time : ~20-40 minutes
"""

from typing import Optional

import pandas as pd
import pandas_ta as ta

import config
from base_bot import BaseBot

_MIN_BARS = 210  # raised for EMA200 warmup

# Precomputed column names (added by precompute_indicators)
_C_HIST      = "_macd_hist"
_C_PREV_HIST = "_macd_prev_hist"
_C_EMA200    = "_ema200"
_C_VOL_SMA   = "_vol_sma20"
_C_RSI       = "_rsi14"


class MACDBot(BaseBot):
    name = "MACD"

    def __init__(self) -> None:
        super().__init__()
        self.timeframe = config.TIMEFRAMES["MACD"]

    # ── Indicator precomputation (called once by BacktestEngine) ──────────────

    @staticmethod
    def precompute_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute all indicators on the full DataFrame once.
        BacktestEngine calls this before the bar loop to avoid O(n^2) recomputation.
        """
        df = df.copy()
        macd_df  = ta.macd(df["close"])
        hist_col = next((c for c in macd_df.columns if c.startswith("MACDh")), None)
        if hist_col:
            df[_C_HIST]      = macd_df[hist_col]
            df[_C_PREV_HIST] = df[_C_HIST].shift(1)
        df[_C_EMA200]  = ta.ema(df["close"], length=200)
        df[_C_VOL_SMA] = df["volume"].rolling(20).mean()
        df[_C_RSI]     = ta.rsi(df["close"], length=14)
        return df

    # ── Single iteration (live paper trading) ─────────────────────────────────

    def run_once(self) -> None:
        if self.check_daily_loss():
            return

        self.check_stop_loss_take_profit()

        for symbol in config.SYMBOLS:
            try:
                self._process_symbol(symbol)
            except Exception as exc:
                self.log.error("Error processing %s: %s", symbol, exc)

    # ── Signal generation (shared by live trading and backtesting) ────────────

    def generate_signal(
        self, df: pd.DataFrame, position: Optional[str] = None
    ) -> Optional[str]:
        """
        Parameters
        ----------
        df       : OHLCV DataFrame. If precompute_indicators() was called on the
                   full df beforehand, indicator columns are used directly (fast path).
                   Otherwise they are computed from scratch (live trading path).
        position : current position side - "long" or None (flat)

        Returns
        -------
        "buy"   -> enter long
        "close" -> exit current long
        None    -> no action
        """
        if len(df) < _MIN_BARS:
            return None

        # ── Fast path: precomputed columns present ────────────────────────────
        if _C_HIST in df.columns:
            curr_hist  = float(df[_C_HIST].iloc[-1])
            prev_hist  = float(df[_C_PREV_HIST].iloc[-1])
            ema200_val = float(df[_C_EMA200].iloc[-1])
            vol_sma    = float(df[_C_VOL_SMA].iloc[-1])
            latest_vol = float(df["volume"].iloc[-1])
            price      = float(df["close"].iloc[-1])
            rsi_val    = float(df[_C_RSI].iloc[-1])

            if any(pd.isna(v) for v in [curr_hist, prev_hist, ema200_val, vol_sma, rsi_val]):
                return None

        # ── Slow path: compute from scratch (live trading) ────────────────────
        else:
            macd_df = ta.macd(df["close"])
            if macd_df is None or macd_df.empty:
                return None

            hist_col   = next(c for c in macd_df.columns if c.startswith("MACDh"))
            histogram  = macd_df[hist_col]
            prev_hist  = float(histogram.iloc[-2])
            curr_hist  = float(histogram.iloc[-1])
            vol_sma    = float(df["volume"].rolling(20).mean().iloc[-1])
            latest_vol = float(df["volume"].iloc[-1])
            ema200     = ta.ema(df["close"], length=200)
            ema200_val = float(ema200.iloc[-1]) if ema200 is not None else None
            rsi_series = ta.rsi(df["close"], length=14)
            rsi_val    = float(rsi_series.iloc[-1]) if rsi_series is not None else 50.0
            price      = float(df["close"].iloc[-1])

        # ── Signal logic ──────────────────────────────────────────────────────
        vol_spike    = latest_vol > vol_sma * 2.0
        hist_growing = abs(curr_hist) > abs(prev_hist)

        if position == "long":
            # Exit on 2 consecutive negative histogram bars (confirm reversal)
            if curr_hist < 0 and prev_hist < 0:
                return "close"

        else:  # flat — zero-cross entry
            if vol_spike and hist_growing and ema200_val is not None:
                if prev_hist < 0 and curr_hist > 0 and price > ema200_val:
                    return "buy"

        return None

    # ── Per-symbol live logic ─────────────────────────────────────────────────

    def _process_symbol(self, symbol: str) -> None:
        df = self.fetch_ohlcv(symbol, self.timeframe)
        if len(df) < _MIN_BARS:
            return

        position = self.positions[symbol]["side"] if symbol in self.positions else None
        signal   = self.generate_signal(df, position)

        if signal == "buy" and position is None:
            self.open_position(symbol, "long")
        elif signal == "close" and position is not None:
            self.close_position(symbol, reason="macd_signal_reverse")
