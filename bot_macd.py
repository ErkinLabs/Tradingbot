"""
Bot 1 — MACD Momentum Strategy (Spot)
=======================================
Timeframe  : 5-minute candles
Signal     : MACD histogram zero-cross
              prev_hist < 0 AND curr_hist > 0  ->  BUY  (long)
Exit (patient):
  1. Trailing stop  : price falls > 1.2% from peak-since-entry  → close
  2. 2-bar confirm  : histogram negative for 2 consecutive bars
                      AND MACD line < 0                          → close
  3. Min hold       : neither exit fires before 3 bars have passed
Filters:
  1. EMA200 trend: long only above EMA200
  2. ADX >= 20: confirmed trending market (filters choppy/sideways regimes)
  3. MACD line > 0: fast EMA above slow EMA (double-cross confirmation)
  4. Volume spike: current volume > 20-bar SMA * 2.0
  5. Histogram momentum: |histogram| must be growing (signal gaining strength)
  6. RSI 45-75: not oversold, not overbought at entry
Target win rate: ~50 %
Avg hold time : ~30-60 minutes
"""

from datetime import datetime, timezone
from typing import Optional, Union

import pandas as pd
import pandas_ta as ta

import config
from base_bot import BaseBot

_MIN_BARS      = 210    # raised for EMA200 warmup
_MIN_HOLD_BARS = 3      # don't exit before 3 bars (15 min on 5m TF)
_TRAILING_PCT  = 0.012  # 1.2% drawdown from peak triggers trailing close

# Precomputed column names (added by precompute_indicators)
_C_HIST       = "_macd_hist"
_C_PREV_HIST  = "_macd_prev_hist"
_C_PREV2_HIST = "_macd_prev2_hist"
_C_EMA200     = "_ema200"
_C_VOL_SMA    = "_vol_sma20"
_C_RSI        = "_rsi14"
_C_ADX        = "_adx14"
_C_MACD_LINE  = "_macd_line"

# Timeframe → seconds (for bars_held computation in live trading)
_TF_SECS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600}


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
        hist_col      = next((c for c in macd_df.columns if c.startswith("MACDh")), None)
        macd_line_col = next((c for c in macd_df.columns if c.startswith("MACD_")), None)
        if hist_col:
            df[_C_HIST]       = macd_df[hist_col]
            df[_C_PREV_HIST]  = df[_C_HIST].shift(1)
            df[_C_PREV2_HIST] = df[_C_HIST].shift(2)
        if macd_line_col:
            df[_C_MACD_LINE]  = macd_df[macd_line_col]
        df[_C_EMA200]  = ta.ema(df["close"], length=200)
        df[_C_VOL_SMA] = df["volume"].rolling(20).mean()
        df[_C_RSI]     = ta.rsi(df["close"], length=14)
        df[_C_ADX]     = ta.adx(df["high"], df["low"], df["close"], length=14)["ADX_14"]
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
        self,
        df: pd.DataFrame,
        position: Union[None, str, dict] = None,
    ) -> Optional[str]:
        """
        Parameters
        ----------
        df       : OHLCV DataFrame. If precompute_indicators() was called on the
                   full df beforehand, indicator columns are used directly (fast path).
                   Otherwise they are computed from scratch (live trading path).
        position : None (flat), "long" (legacy string), or a dict:
                     {"side": "long", "bars_held": int, "peak_price": float}
                   BacktestEngine and _process_symbol pass the dict form;
                   old callers passing a bare string continue to work.

        Returns
        -------
        "buy"   -> enter long
        "close" -> exit current long
        None    -> no action
        """
        if len(df) < _MIN_BARS:
            return None

        # ── Unpack position context ───────────────────────────────────────────
        if isinstance(position, dict):
            pos_side   = position.get("side")
            bars_held  = int(position.get("bars_held", 0))
            peak_price = float(position.get("peak_price") or 0.0)
        else:
            pos_side   = position   # None or "long"
            bars_held  = 0
            peak_price = 0.0

        # ── Fast path: precomputed columns present ────────────────────────────
        if _C_HIST in df.columns:
            curr_hist  = float(df[_C_HIST].iloc[-1])
            prev_hist  = float(df[_C_PREV_HIST].iloc[-1])
            prev2_hist = float(df[_C_PREV2_HIST].iloc[-1])
            ema200_val = float(df[_C_EMA200].iloc[-1])
            vol_sma    = float(df[_C_VOL_SMA].iloc[-1])
            latest_vol = float(df["volume"].iloc[-1])
            price      = float(df["close"].iloc[-1])
            rsi_val    = float(df[_C_RSI].iloc[-1])
            adx_val    = float(df[_C_ADX].iloc[-1])
            macd_line  = float(df[_C_MACD_LINE].iloc[-1])

            if any(pd.isna(v) for v in [curr_hist, prev_hist, prev2_hist, ema200_val, vol_sma, rsi_val, adx_val, macd_line]):
                return None

        # ── Slow path: compute from scratch (live trading) ────────────────────
        else:
            macd_df = ta.macd(df["close"])
            if macd_df is None or macd_df.empty:
                return None

            hist_col      = next(c for c in macd_df.columns if c.startswith("MACDh"))
            macd_line_col = next(c for c in macd_df.columns if c.startswith("MACD_"))
            histogram  = macd_df[hist_col]
            prev2_hist = float(histogram.iloc[-3])
            prev_hist  = float(histogram.iloc[-2])
            curr_hist  = float(histogram.iloc[-1])
            macd_line  = float(macd_df[macd_line_col].iloc[-1])
            vol_sma    = float(df["volume"].rolling(20).mean().iloc[-1])
            latest_vol = float(df["volume"].iloc[-1])
            ema200     = ta.ema(df["close"], length=200)
            ema200_val = float(ema200.iloc[-1]) if ema200 is not None else None
            rsi_series = ta.rsi(df["close"], length=14)
            rsi_val    = float(rsi_series.iloc[-1]) if rsi_series is not None else 50.0
            adx_df     = ta.adx(df["high"], df["low"], df["close"], length=14)
            adx_val    = float(adx_df["ADX_14"].iloc[-1]) if adx_df is not None else 0.0
            price      = float(df["close"].iloc[-1])

        # ── Signal logic ──────────────────────────────────────────────────────
        vol_spike    = latest_vol > vol_sma * 2.0
        hist_growing = abs(curr_hist) > abs(prev_hist)

        if pos_side == "long":
            # Minimum hold: suppress all exits for the first N bars
            if bars_held < _MIN_HOLD_BARS:
                return None

            # Trailing stop: close if price pulled back > 1.2% from the highest
            # close seen since entry (peak_price is maintained by the caller)
            if peak_price > 0 and price < peak_price * (1 - _TRAILING_PCT):
                return "close"

            # 2-bar confirmation exit: histogram must be negative for two
            # consecutive bars AND MACD line must be below zero.
            # Single-bar dips are ignored to avoid premature exits.
            if macd_line < 0 and curr_hist < 0 and prev_hist < 0:
                return "close"

        else:  # flat — zero-cross entry
            if vol_spike and hist_growing and ema200_val is not None:
                if prev_hist < 0 and curr_hist > 0 and price > ema200_val:
                    if 45 <= rsi_val <= 75 and adx_val >= 20 and macd_line > 0:
                        return "buy"

        return None

    # ── Per-symbol live logic ─────────────────────────────────────────────────

    def _process_symbol(self, symbol: str) -> None:
        if symbol not in self._buffers:
            self.log.debug("Buffer not yet attached for %s — skipping.", symbol)
            return
        df = self._buffers[symbol].get_df()
        if len(df) < _MIN_BARS:
            return

        pos_context: Optional[dict] = None

        if symbol in self.positions:
            pos   = self.positions[symbol]
            price = float(df["close"].iloc[-1])

            # Keep a running peak price in the live position dict
            if "peak_price" not in pos:
                pos["peak_price"] = pos["entry_price"]
            pos["peak_price"] = max(pos["peak_price"], price)

            # Derive bars_held from elapsed wall-clock time and the TF duration
            tf_secs   = _TF_SECS.get(self.timeframe, 300)
            elapsed   = (datetime.now(timezone.utc) - pos["opened_at"]).total_seconds()
            bars_held = max(0, int(elapsed / tf_secs))

            pos_context = {
                "side":       pos["side"],
                "bars_held":  bars_held,
                "peak_price": pos["peak_price"],
            }

        signal = self.generate_signal(df, pos_context)

        if signal == "buy" and pos_context is None:
            self.open_position(symbol, "long")
        elif signal == "close" and pos_context is not None:
            self.close_position(symbol, reason="macd_signal_reverse")
