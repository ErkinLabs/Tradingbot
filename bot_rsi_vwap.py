"""
Bot 2 — RSI + VWAP Mean-Reversion Strategy (Spot)
===================================================
Timeframe  : 1-hour candles
Signal
  LONG  : RSI < 30  AND price < VWAP   -> buy into oversold crowd
  CLOSE : RSI > 60                     -> mean reversion complete, exit
VWAP   : resets at midnight UTC each day (cumulative within-day calculation)
Filters:
  1. VWAP deviation: price must be >0.3% away from VWAP (meaningful extreme)
  2. RSI turning: RSI must have troughed (prev < curr for long)
  3. ADX < 40: avoid entering mean-reversion during strong trending markets
Target win rate: ~60 %
Avg hold time : ~4-6 hours
"""

from typing import Optional

import pandas as pd
import pandas_ta as ta

import config
from base_bot import BaseBot

_MIN_BARS = 20

# Precomputed column names
_C_RSI       = "_rsi14"
_C_PREV_RSI  = "_prev_rsi14"
_C_PREV2_RSI = "_prev2_rsi14"
_C_VWAP      = "_vwap"
_C_ADX       = "_adx14"


def _calc_vwap(df: pd.DataFrame) -> pd.Series:
    """
    Daily-resetting VWAP using typical price x volume.
    Resets at midnight UTC so each day starts fresh.
    """
    typical = (df["high"] + df["low"] + df["close"]) / 3

    if isinstance(df.index, pd.DatetimeIndex):
        date_key = df.index.normalize()
        pv   = (typical * df["volume"]).groupby(date_key).cumsum()
        cvol = df["volume"].groupby(date_key).cumsum()
    else:
        pv   = (typical * df["volume"]).cumsum()
        cvol = df["volume"].cumsum()

    return pv / cvol.replace(0, float("nan"))


class RSIVWAPBot(BaseBot):
    name = "RSI_VWAP"

    def __init__(self) -> None:
        super().__init__()
        self.timeframe = config.TIMEFRAMES["RSI_VWAP"]

    # ── Indicator precomputation (called once by BacktestEngine) ──────────────

    @staticmethod
    def precompute_indicators(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df[_C_RSI]       = ta.rsi(df["close"], length=14)
        df[_C_PREV_RSI]  = df[_C_RSI].shift(1)
        df[_C_PREV2_RSI] = df[_C_RSI].shift(2)
        df[_C_VWAP]      = _calc_vwap(df)

        adx_df  = ta.adx(df["high"], df["low"], df["close"], length=14)
        adx_col = next((c for c in adx_df.columns if c.startswith("ADX_")), None)
        df[_C_ADX] = adx_df[adx_col] if adx_col else 0.0
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

    # ── Signal generation ─────────────────────────────────────────────────────

    def generate_signal(
        self, df: pd.DataFrame, position: Optional[str] = None
    ) -> Optional[str]:
        """
        Parameters
        ----------
        df       : OHLCV DataFrame (with or without precomputed indicator columns)
        position : "long" or None

        Returns
        -------
        "buy"   -> enter long
        "close" -> exit long
        None    -> hold
        """
        if len(df) < _MIN_BARS:
            return None

        # ── Fast path ─────────────────────────────────────────────────────────
        if _C_RSI in df.columns:
            rsi       = float(df[_C_RSI].iloc[-1])
            prev_rsi  = float(df[_C_PREV_RSI].iloc[-1])
            prev2_rsi = float(df[_C_PREV2_RSI].iloc[-1])
            price     = float(df["close"].iloc[-1])
            bar_open  = float(df["open"].iloc[-1])
            vwap      = float(df[_C_VWAP].iloc[-1])
            adx_val   = float(df[_C_ADX].iloc[-1])

            if any(pd.isna(v) for v in [rsi, prev_rsi, prev2_rsi, vwap]):
                return None

        # ── Slow path (live trading) ──────────────────────────────────────────
        else:
            rsi_series = ta.rsi(df["close"], length=14)
            if rsi_series is None:
                return None

            vwap_series = _calc_vwap(df)
            rsi       = float(rsi_series.iloc[-1])
            prev_rsi  = float(rsi_series.iloc[-2])
            prev2_rsi = float(rsi_series.iloc[-3])
            price     = float(df["close"].iloc[-1])
            bar_open  = float(df["open"].iloc[-1])
            vwap      = float(vwap_series.iloc[-1])

            if any(pd.isna(v) for v in [rsi, prev_rsi, prev2_rsi, vwap]):
                return None

            adx_df  = ta.adx(df["high"], df["low"], df["close"], length=14)
            if adx_df is not None and not adx_df.empty:
                adx_col = next((c for c in adx_df.columns if c.startswith("ADX_")), None)
                adx_val = float(adx_df[adx_col].iloc[-1]) if adx_col else 0.0
            else:
                adx_val = 0.0

        # ── Signal logic ──────────────────────────────────────────────────────
        vwap_dev = abs(price - vwap) / vwap if vwap != 0 else 0.0

        if position == "long":
            if rsi > 55:
                return "close"

        else:  # flat
            trend_calm    = adx_val < 40
            vwap_extreme  = vwap_dev > 0.005
            rsi_turning   = prev_rsi < rsi               # RSI troughed (1-bar)
            bullish_close = price > bar_open              # entry bar closing positive

            if trend_calm and vwap_extreme and rsi_turning and bullish_close:
                if rsi < 30 and price < vwap:
                    return "buy"

        return None

    # ── Per-symbol live logic ─────────────────────────────────────────────────

    def _process_symbol(self, symbol: str) -> None:
        if symbol not in self._buffers:
            self.log.debug("Buffer not yet attached for %s — skipping.", symbol)
            return
        df = self._buffers[symbol].get_df()
        n  = len(df)
        if n < _MIN_BARS:
            self.log.debug("RSI_VWAP %s | bars=%d/%d — warming up", symbol, n, _MIN_BARS)
            return

        position = self.positions[symbol]["side"] if symbol in self.positions else None
        signal   = self.generate_signal(df, position)

        # ── DEBUG: indicator snapshot every candle close ──────────────────────
        try:
            _rsi_s  = ta.rsi(df["close"], length=14)
            _rsi    = float(_rsi_s.iloc[-1])
            _prsi   = float(_rsi_s.iloc[-2])
            _vwap   = float(_calc_vwap(df).iloc[-1])
            _price  = float(df["close"].iloc[-1])
            _bopen  = float(df["open"].iloc[-1])
            _adxd   = ta.adx(df["high"], df["low"], df["close"], length=14)
            _ac     = next((c for c in _adxd.columns if c.startswith("ADX_")), None)
            _adx    = float(_adxd[_ac].iloc[-1]) if _ac else 0.0
            _dev    = abs(_price - _vwap) / _vwap * 100 if _vwap else 0.0
            _why = ""
            if signal is None and position is None:
                if _rsi >= 30:          _why = f"rsi={_rsi:.1f}≥30"
                elif _price >= _vwap:   _why = f"price≥vwap({_price:.4f}≥{_vwap:.4f})"
                elif _adx >= 40:        _why = f"adx={_adx:.1f}≥40"
                elif _dev <= 0.5:       _why = f"vwap_dev={_dev:.3f}%≤0.5%"
                elif _prsi >= _rsi:     _why = f"rsi-not-turning({_prsi:.1f}→{_rsi:.1f})"
                elif _price <= _bopen:  _why = "bar-closing-down"
            self.log.debug(
                "RSI_VWAP %s | rsi=%.1f prev=%.1f vwap=%.4f price=%.4f "
                "dev=%.3f%% adx=%.1f pos=%s → %s%s",
                symbol, _rsi, _prsi, _vwap, _price, _dev, _adx,
                "long" if position else "flat",
                signal or "no_signal",
                f" ({_why})" if _why else "",
            )
        except Exception:
            pass

        if signal == "buy" and position is None:
            self.open_position(symbol, "long")
        elif signal == "close" and position is not None:
            self.close_position(symbol, reason="rsi_mean_reversion")
