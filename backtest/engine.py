"""
engine.py — Core bar-by-bar backtesting engine (Spot).

Design rules
  - Long-only: "buy" opens a long; "sell" or "close" exits it
  - No lookahead: strategy only sees df.iloc[:i+1] at bar i
  - Fills at NEXT bar's open (1-bar execution delay)
  - SL/TP checked within each bar using high/low (fills at exact SL/TP price)
  - Commission deducted on both open AND close
  - Daily loss limit enforced: if breached, signals are suppressed for the day
  - At end-of-data any open position is force-closed at the last close
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

import pandas as pd

import config


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class Trade:
    symbol:      str
    side:        str       # "long" | "short"
    entry_time:  Any       # pd.Timestamp
    exit_time:   Any
    entry_price: float
    exit_price:  float
    size:        float     # base-currency units (e.g. BTC)
    gross_pnl:   float     # price-change PnL before commission
    commission:  float     # total commission (entry + exit)
    net_pnl:     float     # gross_pnl - commission
    reason:      str       # "stop_loss" | "take_profit" | "signal" | "end_of_data"
    hold_bars:   int


@dataclass
class BacktestResult:
    strategy_name:   str
    symbol:          str
    timeframe:       str
    start_date:      str
    end_date:        str
    initial_balance: float
    final_balance:   float
    trades:          list[Trade]
    equity_curve:    list[tuple]   # [(pd.Timestamp, float), …]
    metrics:         dict = field(default_factory=dict)


# ── Position and pending-order helpers ────────────────────────────────────────

def _unrealized(position: Optional[dict], price: float) -> float:
    if position is None:
        return 0.0
    if position["side"] == "long":
        return (price - position["entry_price"]) * position["size"]
    return (position["entry_price"] - price) * position["size"]


def _calc_sl_tp_fill(position: dict, bar: pd.Series) -> Optional[tuple[float, str]]:
    """
    Return (fill_price, reason) if SL or TP is triggered within this bar,
    else None.  Conservative rule: SL wins when both are within range.
    """
    entry = position["entry_price"]
    sl_pct = config.STOP_LOSS_PCT
    tp_pct = config.TAKE_PROFIT_PCT

    if position["side"] == "long":
        sl_price = entry * (1 - sl_pct)
        tp_price = entry * (1 + tp_pct)
        sl_hit = bar["low"]  <= sl_price
        tp_hit = bar["high"] >= tp_price
        if sl_hit:
            return sl_price, "stop_loss"
        if tp_hit:
            return tp_price, "take_profit"

    else:  # short
        sl_price = entry * (1 + sl_pct)
        tp_price = entry * (1 - tp_pct)
        sl_hit = bar["high"] >= sl_price
        tp_hit = bar["low"]  <= tp_price
        if sl_hit:
            return sl_price, "stop_loss"
        if tp_hit:
            return tp_price, "take_profit"

    return None


def _close_calc(
    position: dict, exit_price: float, commission_rate: float
) -> tuple[float, float, float]:
    """Return (gross_pnl, exit_commission, total_commission)."""
    size  = position["size"]
    entry = position["entry_price"]

    gross = (exit_price - entry) * size if position["side"] == "long" \
            else (entry - exit_price) * size

    exit_comm  = exit_price * size * commission_rate
    total_comm = position["entry_commission"] + exit_comm
    return gross, exit_comm, total_comm


# ── Engine ─────────────────────────────────────────────────────────────────────

class BacktestEngine:
    """
    Runs a single strategy on a single symbol's OHLCV DataFrame.

    Parameters
    ----------
    strategy        : bot instance that implements generate_signal(df, position)
    df              : full OHLCV DataFrame (DatetimeIndex, UTC)
    symbol          : trading pair string, e.g. "BTC/USDT"
    initial_balance : starting USDT balance for this bot
    commission_rate : fraction per trade side (default 0.00055 = Bybit taker)
    """

    def __init__(
        self,
        strategy,
        df: pd.DataFrame,
        symbol: str,
        initial_balance: float = 10_000.0,
        commission_rate: float = 0.00055,
    ) -> None:
        self.strategy         = strategy
        self.df               = df.copy()
        self.symbol           = symbol
        self.initial_balance  = initial_balance
        self.commission_rate  = commission_rate

    # ── Main entry ────────────────────────────────────────────────────────────

    def run(self) -> BacktestResult:
        df = self.df
        # Precompute indicators once to avoid O(n²) recomputation per bar
        if hasattr(self.strategy, "precompute_indicators"):
            df = self.strategy.precompute_indicators(df)

        n       = len(df)
        balance = self.initial_balance
        trades: list[Trade]       = []
        equity_curve: list[tuple] = []

        position: Optional[dict]  = None  # open position metadata
        pending_open: Optional[dict] = None  # queued entry for next bar
        pending_close: bool          = False  # queued exit for next bar

        # Daily loss tracking
        current_day:       Optional[date] = None
        day_start_equity:  float          = balance
        day_paused:        bool           = False
        day_trades:        int            = 0

        for i in range(n):
            ts  = df.index[i]
            bar = df.iloc[i]

            # ── Day boundary ──────────────────────────────────────────────────
            bar_day = ts.date()
            if bar_day != current_day:
                current_day      = bar_day
                day_start_equity = balance + _unrealized(position, float(bar["open"]))
                day_paused       = False
                day_trades       = 0

            # ── Process pending CLOSE at this bar's open ──────────────────────
            if pending_close and position is not None:
                exit_price                = float(bar["open"])
                gross, exit_comm, tot_comm = _close_calc(position, exit_price, self.commission_rate)
                balance += gross - exit_comm
                trades.append(Trade(
                    symbol=self.symbol,
                    side=position["side"],
                    entry_time=position["entry_time"],
                    exit_time=ts,
                    entry_price=position["entry_price"],
                    exit_price=exit_price,
                    size=position["size"],
                    gross_pnl=round(gross, 6),
                    commission=round(tot_comm, 6),
                    net_pnl=round(gross - tot_comm, 6),
                    reason=position.get("pending_reason", "signal"),
                    hold_bars=i - position["entry_bar"],
                ))
                position      = None
                pending_close = False

            # ── Process pending OPEN at this bar's open ───────────────────────
            if pending_open is not None and position is None:
                entry_price  = float(bar["open"])
                size         = pending_open["notional"] / entry_price
                entry_comm   = entry_price * size * self.commission_rate
                balance     -= entry_comm
                position = {
                    "side":             pending_open["side"],
                    "entry_price":      entry_price,
                    "entry_time":       ts,
                    "entry_bar":        i,
                    "size":             size,
                    "entry_commission": entry_comm,
                }
                pending_open = None

            # ── SL / TP within this bar ───────────────────────────────────────
            if position is not None and not pending_close:
                fill = _calc_sl_tp_fill(position, bar)
                if fill is not None:
                    exit_price, reason = fill
                    gross, exit_comm, tot_comm = _close_calc(
                        position, exit_price, self.commission_rate
                    )
                    balance += gross - exit_comm
                    trades.append(Trade(
                        symbol=self.symbol,
                        side=position["side"],
                        entry_time=position["entry_time"],
                        exit_time=ts,
                        entry_price=position["entry_price"],
                        exit_price=exit_price,
                        size=position["size"],
                        gross_pnl=round(gross, 6),
                        commission=round(tot_comm, 6),
                        net_pnl=round(gross - tot_comm, 6),
                        reason=reason,
                        hold_bars=i - position["entry_bar"],
                    ))
                    position = None

            # ── Daily loss check ──────────────────────────────────────────────
            current_equity = balance + _unrealized(position, float(bar["close"]))
            if not day_paused and day_start_equity > 0:
                daily_loss_frac = (day_start_equity - current_equity) / day_start_equity
                if daily_loss_frac >= config.MAX_DAILY_LOSS_PCT:
                    day_paused = True

            # ── Generate signal (only when not paused or pending) ─────────────
            if (
                not day_paused
                and pending_open is None
                and not pending_close
                # need at least one more bar to fill the order
                and i < n - 1
            ):
                pos_side = position["side"] if position else None
                try:
                    signal = self.strategy.generate_signal(df.iloc[: i + 1], pos_side)
                except Exception:
                    signal = None

                notional = balance * config.MAX_POSITION_PCT

                if signal == "buy":
                    if position is None and notional > 0:
                        pending_open = {"side": "long", "notional": notional}
                        day_trades  += 1

                elif signal in ("sell", "close"):
                    if position is not None:
                        position["pending_reason"] = "signal"
                        pending_close = True

            # ── Equity curve snapshot ─────────────────────────────────────────
            equity = balance + _unrealized(position, float(bar["close"]))
            equity_curve.append((ts, equity))

        # ── End-of-data: force-close any open position ────────────────────────
        if position is not None:
            last_bar = df.iloc[-1]
            last_ts  = df.index[-1]
            exit_price = float(last_bar["close"])
            gross, exit_comm, tot_comm = _close_calc(position, exit_price, self.commission_rate)
            balance += gross - exit_comm
            trades.append(Trade(
                symbol=self.symbol,
                side=position["side"],
                entry_time=position["entry_time"],
                exit_time=last_ts,
                entry_price=position["entry_price"],
                exit_price=exit_price,
                size=position["size"],
                gross_pnl=round(gross, 6),
                commission=round(tot_comm, 6),
                net_pnl=round(gross - tot_comm, 6),
                reason="end_of_data",
                hold_bars=n - 1 - position["entry_bar"],
            ))
            if equity_curve:
                equity_curve[-1] = (equity_curve[-1][0], balance)

        return BacktestResult(
            strategy_name=self.strategy.name,
            symbol=self.symbol,
            timeframe=getattr(self.strategy, "timeframe", "?"),
            start_date=str(df.index[0].date()),
            end_date=str(df.index[-1].date()),
            initial_balance=self.initial_balance,
            final_balance=round(balance, 4),
            trades=trades,
            equity_curve=equity_curve,
        )
