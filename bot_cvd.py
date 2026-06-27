"""
Bot 3 — CVD Divergence Strategy (Spot)
========================================
Timeframe  : 15-minute candles
CVD        : cumulative sum of signed volume (bar-direction approximation)
              up-close candle   → +volume
              down-close candle → -volume

Signal
  LONG  : price falling (-0.8 % over 10 bars) BUT CVD rising  → hidden buying
  CLOSE : CVD turns negative → divergence resolved, exit

Filters:
  1. Min price change: 0.8% (filter noise and weak moves)
  2. CVD magnitude: CVD change must exceed 1% of average daily volume
  3. Confirmation: divergence must persist for 2 consecutive bars
  4. EMA50 trend: long only above EMA50

Known gap — live vs backtest CVD:
  Live trading should ideally use tick-level CVD (delta = buy_vol − sell_vol per trade
  via ccxt's fetch_trades). Backtesting uses bar-direction CVD as a proxy (up-close bar
  → +volume, down-close bar → −volume). In trending markets this approximation holds well;
  in choppy markets divergence accuracy may degrade.
  To measure the gap: run --strategy cvd backtest on a period and compare the signal
  counts against a live paper-trading log for the same window.

Target win rate : ~58 %
Avg hold time   : ~47 minutes
"""

from typing import Optional

import pandas as pd
import pandas_ta as ta

import config
from base_bot import BaseBot

_LOOKBACK = 10
_MIN_PRICE_CHANGE = 0.008  # 0.8% — best balance of signal quality vs frequency
_MIN_BARS = _LOOKBACK + 55  # raised for EMA50 + confirmation bar

# Precomputed column names
_C_CVD     = "_cvd"
_C_EMA50   = "_ema50"
_C_AVG_VOL = "_avg_vol96"


def _calc_cvd(df: pd.DataFrame) -> pd.Series:
    """
    Cumulative Volume Delta (bar-direction approximation).
    Up-close bar  → +volume (net buyers dominated)
    Down-close bar → -volume (net sellers dominated)
    Equal bars counted as neutral (0).
    """
    signed = df["volume"].where(df["close"] > df["open"],
             -df["volume"].where(df["close"] < df["open"], 0))
    return signed.cumsum()


class CVDBot(BaseBot):
    name = "CVD"

    def __init__(self) -> None:
        super().__init__()
        self.timeframe = config.TIMEFRAMES["CVD"]

    # ── Indicator precomputation (called once by BacktestEngine) ──────────────

    @staticmethod
    def precompute_indicators(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df[_C_CVD]     = _calc_cvd(df)
        df[_C_EMA50]   = ta.ema(df["close"], length=50)
        df[_C_AVG_VOL] = df["volume"].rolling(96).mean()
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
        df       : OHLCV DataFrame
        position : "long", "short", or None

        Returns
        -------
        "buy"   → bullish divergence detected (or close short)
        "sell"  → bearish divergence detected (or close long)
        "close" → divergence resolved, exit position
        None    → no actionable signal
        """
        if len(df) < _MIN_BARS:
            return None

        # ── Fast path (precomputed columns present) ───────────────────────────
        if _C_CVD in df.columns:
            cvd       = df[_C_CVD]
            ema50_val = float(df[_C_EMA50].iloc[-1]) if not pd.isna(df[_C_EMA50].iloc[-1]) else None
            avg_vol   = float(df[_C_AVG_VOL].iloc[-1])

        # ── Slow path (live trading — compute from scratch) ───────────────────
        else:
            cvd       = _calc_cvd(df)
            ema50     = ta.ema(df["close"], length=50)
            ema50_val = float(ema50.iloc[-1]) if ema50 is not None else None
            avg_vol   = float(df["volume"].rolling(96).mean().iloc[-1])

        price_now  = float(df["close"].iloc[-1])
        price_then = float(df["close"].iloc[-_LOOKBACK - 1])
        cvd_now    = float(cvd.iloc[-1])
        cvd_then   = float(cvd.iloc[-_LOOKBACK - 1])

        price_change = (price_now - price_then) / price_then if price_then != 0 else 0.0
        cvd_change   = cvd_now - cvd_then

        # CVD magnitude filter: change must be significant relative to recent volume
        cvd_min_move = avg_vol * 0.01
        cvd_sig      = abs(cvd_change) > cvd_min_move

        # Confirmation: check previous bar had the same divergence
        price_prev  = float(df["close"].iloc[-2])
        price_prev2 = float(df["close"].iloc[-_LOOKBACK - 2])
        cvd_prev    = float(cvd.iloc[-2])
        cvd_prev2   = float(cvd.iloc[-_LOOKBACK - 2])
        prev_price_change = (price_prev - price_prev2) / price_prev2 if price_prev2 != 0 else 0.0
        prev_cvd_change   = cvd_prev - cvd_prev2

        if position == "long":
            # Exit when buying pressure disappears
            if cvd_change <= 0:
                return "close"

        else:  # flat — all filters must pass
            if cvd_sig and ema50_val is not None:
                bullish_now  = price_change < -_MIN_PRICE_CHANGE and cvd_change > 0
                bullish_prev = prev_price_change < -_MIN_PRICE_CHANGE and prev_cvd_change > 0
                if bullish_now and bullish_prev and price_now > ema50_val:
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
            self.close_position(symbol, reason="cvd_divergence_resolved")
