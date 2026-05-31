"""
Bot 3 — CVD Divergence Strategy (Spot)
========================================
Timeframe  : 15-minute candles
CVD        : cumulative sum of signed volume (bar-direction approximation)
              up-close candle   → +volume
              down-close candle → -volume

Signal
  LONG  : price falling (-0.8 % over 10 bars) BUT CVD rising  → hidden buying
  CLOSE :
    1. Trailing stop  : price falls > 1.2% from peak-since-entry  → close
    2. 2-bar confirm  : CVD change ≤ 0 for two consecutive bars   → close
    3. Min hold       : neither exit fires before 3 bars have passed

Filters:
  1. Min price change: 0.8% (filter noise and weak moves)
  2. CVD magnitude: CVD change must exceed 1% of average daily volume
  3. Confirmation: divergence must persist for 2 consecutive bars
  4. EMA200 trend: long only above EMA200

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

from datetime import datetime, timezone
from typing import Optional, Union

import pandas as pd
import pandas_ta as ta

import config
from base_bot import BaseBot
from cvd_utils import calc_cvd_bar_direction

_LOOKBACK         = 10
_MIN_PRICE_CHANGE = 0.008  # 0.8% — best balance of signal quality vs frequency
_MIN_BARS         = _LOOKBACK + 205  # raised for EMA200 + confirmation bar
_MIN_HOLD_BARS    = 3       # don't exit before 3 bars (45 min on 15m TF)
_TRAILING_PCT     = 0.012   # 1.2% drawdown from peak triggers trailing close

# Precomputed column names
_C_CVD      = "_cvd"
_C_EMA200   = "_ema200"
_C_AVG_VOL  = "_avg_vol96"

# Timeframe → seconds (for bars_held computation in live trading)
_TF_SECS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600}


def _calc_cvd(df: pd.DataFrame) -> pd.Series:
    """Bar-direction CVD proxy (see cvd_utils.calc_cvd_trade_delta for trade-level)."""
    return calc_cvd_bar_direction(df)


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
        df[_C_EMA200]  = ta.ema(df["close"], length=200)
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
        self,
        df: pd.DataFrame,
        position: Union[None, str, dict] = None,
    ) -> Optional[str]:
        """
        Parameters
        ----------
        df       : OHLCV DataFrame
        position : None (flat), "long" (legacy string), or a dict:
                     {"side": "long", "bars_held": int, "peak_price": float}
                   BacktestEngine and _process_symbol pass the dict form;
                   old callers passing a bare string continue to work.

        Returns
        -------
        "buy"   → bullish divergence detected
        "close" → divergence resolved or trailing stop hit, exit position
        None    → no actionable signal
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

        # ── Fast path (precomputed columns present) ───────────────────────────
        if _C_CVD in df.columns:
            cvd        = df[_C_CVD]
            ema200_val = float(df[_C_EMA200].iloc[-1]) if not pd.isna(df[_C_EMA200].iloc[-1]) else None
            avg_vol    = float(df[_C_AVG_VOL].iloc[-1])

        # ── Slow path (live trading — compute from scratch) ───────────────────
        else:
            cvd        = _calc_cvd(df)
            ema200     = ta.ema(df["close"], length=200)
            ema200_val = float(ema200.iloc[-1]) if ema200 is not None else None
            avg_vol    = float(df["volume"].rolling(96).mean().iloc[-1])

        price_now  = float(df["close"].iloc[-1])
        price_then = float(df["close"].iloc[-_LOOKBACK - 1])
        cvd_now    = float(cvd.iloc[-1])
        cvd_then   = float(cvd.iloc[-_LOOKBACK - 1])

        price_change = (price_now - price_then) / price_then if price_then != 0 else 0.0
        cvd_change   = cvd_now - cvd_then

        # CVD magnitude filter: change must be significant relative to recent volume
        cvd_min_move = avg_vol * 0.01
        cvd_sig      = abs(cvd_change) > cvd_min_move

        # Previous bar's divergence metrics (used for entry confirmation AND exit confirmation)
        price_prev  = float(df["close"].iloc[-2])
        price_prev2 = float(df["close"].iloc[-_LOOKBACK - 2])
        cvd_prev    = float(cvd.iloc[-2])
        cvd_prev2   = float(cvd.iloc[-_LOOKBACK - 2])
        prev_price_change = (price_prev - price_prev2) / price_prev2 if price_prev2 != 0 else 0.0
        prev_cvd_change   = cvd_prev - cvd_prev2

        if pos_side == "long":
            # Minimum hold: suppress all exits for the first N bars
            if bars_held < _MIN_HOLD_BARS:
                return None

            # Trailing stop: close if price pulled back > 1.2% from the highest
            # close seen since entry (peak_price is maintained by the caller)
            if peak_price > 0 and price_now < peak_price * (1 - _TRAILING_PCT):
                return "close"

            # 2-bar confirmation exit: CVD change must be ≤ 0 for two consecutive
            # bars before exiting. Single-bar CVD dips are ignored.
            if cvd_change <= 0 and prev_cvd_change <= 0:
                return "close"

        else:  # flat — all filters must pass
            if cvd_sig and ema200_val is not None:
                bullish_now  = price_change < -_MIN_PRICE_CHANGE and cvd_change > 0
                bullish_prev = prev_price_change < -_MIN_PRICE_CHANGE and prev_cvd_change > 0
                if bullish_now and bullish_prev and price_now > ema200_val:
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
            self.log.debug("CVD %s | bars=%d/%d — warming up", symbol, n, _MIN_BARS)
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
            tf_secs   = _TF_SECS.get(self.timeframe, 900)
            elapsed   = (datetime.now(timezone.utc) - pos["opened_at"]).total_seconds()
            bars_held = max(0, int(elapsed / tf_secs))

            pos_context = {
                "side":       pos["side"],
                "bars_held":  bars_held,
                "peak_price": pos["peak_price"],
            }

        signal = self.generate_signal(df, pos_context)

        # ── DEBUG: indicator snapshot every candle close ──────────────────────
        try:
            _cvd   = _calc_cvd(df)
            _e200  = ta.ema(df["close"], length=200)
            _ema   = float(_e200.iloc[-1]) if _e200 is not None else float("nan")
            _avol  = float(df["volume"].rolling(96).mean().iloc[-1])
            _pnow  = float(df["close"].iloc[-1])
            _pthn  = float(df["close"].iloc[-_LOOKBACK - 1])
            _cnow  = float(_cvd.iloc[-1])
            _cthn  = float(_cvd.iloc[-_LOOKBACK - 1])
            _pchg  = (_pnow - _pthn) / _pthn * 100 if _pthn else 0.0
            _cchg  = _cnow - _cthn
            _csig  = abs(_cchg) > _avol * 0.01
            _pprev = float(df["close"].iloc[-2])
            _pp2   = float(df["close"].iloc[-_LOOKBACK - 2])
            _cprev = float(_cvd.iloc[-2])
            _cp2   = float(_cvd.iloc[-_LOOKBACK - 2])
            _ppchg = (_pprev - _pp2) / _pp2 * 100 if _pp2 else 0.0
            _pcchg = _cprev - _cp2
            _why = ""
            if signal is None and pos_context is None:
                if not _csig:                              _why = f"cvd_insig(chg={_cchg:.1f},min={_avol*0.01:.1f})"
                elif pd.isna(_ema) or _pnow < _ema:        _why = f"below-ema200({_pnow:.2f}<{_ema:.2f})"
                elif _pchg >= -_MIN_PRICE_CHANGE * 100:    _why = f"price_chg={_pchg:.3f}%>-0.8%"
                elif _cchg <= 0:                           _why = f"cvd-not-rising({_cchg:.1f})"
                elif not (_ppchg < -_MIN_PRICE_CHANGE * 100 and _pcchg > 0):
                                                           _why = "need-2-bar-confirm"
            self.log.debug(
                "CVD %s | price_chg=%.3f%% prev_chg=%.3f%% cvd_chg=%.2f "
                "prev_cvd_chg=%.2f sig=%s ema200=%.2f pos=%s → %s%s",
                symbol, _pchg, _ppchg, _cchg, _pcchg, _csig, _ema,
                "long" if pos_context else "flat",
                signal or "no_signal",
                f" ({_why})" if _why else "",
            )
        except Exception:
            pass

        if signal == "buy" and pos_context is None:
            self.open_position(symbol, "long")
        elif signal == "close" and pos_context is not None:
            self.close_position(symbol, reason="cvd_divergence_resolved")
