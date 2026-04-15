"""
BaseBot — shared infrastructure inherited by all strategy bots.

Handles:
  - ccxt exchange connection (public API, no auth)
  - OHLCV / ticker fetching with retry
  - Simulated position management (paper trading only)
  - Stop-loss / take-profit checks
  - Commission deduction (both open and close)
  - Daily trade limit enforcement
  - Thread-safe access to positions and trade history
  - Trade logging (CSV + Python logging with rotation)
  - State persistence (positions + trades survive restarts)
  - Performance statistics (trades, win rate, PnL, Sharpe, balance)
"""

import csv
import json
import logging
import logging.handlers
import math
import os
import threading
import time
from datetime import date, datetime, timezone
from typing import Dict, List, Optional

import ccxt
import pandas as pd

import config
from kline_buffer import KlineBuffer


# ── Logging ───────────────────────────────────────────────────────────────────

def _make_logger(name: str) -> logging.Logger:
    os.makedirs(config.LOG_DIR, exist_ok=True)
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s")

    # Rotating file handler — max 5 MB, keep 3 backups
    fh = logging.handlers.RotatingFileHandler(
        config.APP_LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger


# ── CSV trade log ─────────────────────────────────────────────────────────────

_CSV_HEADERS = [
    "timestamp", "bot_name", "symbol", "side",
    "entry_price", "exit_price", "size", "gross_pnl", "commission", "net_pnl", "reason",
]
_csv_lock = threading.Lock()


def _ensure_csv() -> None:
    os.makedirs(config.LOG_DIR, exist_ok=True)
    if not os.path.exists(config.TRADE_LOG_FILE):
        with open(config.TRADE_LOG_FILE, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=_CSV_HEADERS)
            writer.writeheader()


def _append_trade_csv(row: dict) -> None:
    _ensure_csv()
    with _csv_lock:
        with open(config.TRADE_LOG_FILE, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=_CSV_HEADERS)
            writer.writerow(row)


# ── State persistence ─────────────────────────────────────────────────────────

def _state_path(bot_name: str) -> str:
    os.makedirs(config.LOG_DIR, exist_ok=True)
    return os.path.join(config.LOG_DIR, f"state_{bot_name}.json")


def _load_state(bot_name: str) -> dict:
    path = _state_path(bot_name)
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(bot_name: str, positions: dict, closed_trades: list, balance: float) -> None:
    """Persist bot state to disk. Called after every open/close."""
    path = _state_path(bot_name)
    state = {
        "balance": balance,
        "positions": {
            sym: {**pos, "opened_at": pos["opened_at"].isoformat()}
            for sym, pos in positions.items()
        },
        "closed_trades": closed_trades,
    }
    try:
        with open(path, "w") as f:
            json.dump(state, f, indent=2)
    except OSError:
        pass  # non-fatal; next save will retry


def _restore_state(raw: dict) -> tuple[dict, list, Optional[float]]:
    """Convert loaded JSON back to typed structures."""
    positions = {}
    for sym, pos in raw.get("positions", {}).items():
        pos = dict(pos)
        pos["opened_at"] = datetime.fromisoformat(pos["opened_at"])
        positions[sym] = pos

    closed_trades = raw.get("closed_trades", [])
    balance = raw.get("balance")
    return positions, closed_trades, balance


# ── Position helper ───────────────────────────────────────────────────────────

def _new_position(symbol: str, side: str, entry_price: float, size: float) -> dict:
    return {
        "symbol":      symbol,
        "side":        side,
        "entry_price": entry_price,
        "size":        size,
        "opened_at":   datetime.now(timezone.utc),
    }


# ── BaseBot ───────────────────────────────────────────────────────────────────

class BaseBot:
    """Abstract base class — subclasses must implement run_once()."""

    name: str = "BaseBot"

    def __init__(self) -> None:
        assert config.PAPER_TRADING, "PAPER_TRADING must be True — real orders are disabled."

        self.log = _make_logger(self.name)

        # Thread locks — must be held when reading/writing positions or closed_trades
        self._positions_lock = threading.Lock()
        self._trades_lock    = threading.Lock()

        # Restore persisted state (or start fresh)
        raw = _load_state(self.name)
        self.positions, self.closed_trades, saved_balance = _restore_state(raw)

        alloc = config.BOT_ALLOCATIONS.get(self.name, 0.33)
        default_balance = config.INITIAL_BALANCE * alloc

        self.balance: float       = saved_balance if saved_balance is not None else default_balance
        self.start_balance: float = default_balance  # always the configured start, not restored

        # Daily-loss / daily-trade guard
        self._day_start_balance: float = self.balance
        self._current_day: date        = date.today()
        self.paused: bool              = False
        self._day_trade_count: int     = 0

        # Exchange (public, no API keys) — used only for startup warmup
        self.exchange = ccxt.bybit(config.EXCHANGE_OPTS)
        self.exchange.load_markets()

        # WebSocket kline buffers — keyed by symbol, attached by main.py at startup
        self._buffers: dict[str, KlineBuffer] = {}

        restored = len(self.positions) > 0 or len(self.closed_trades) > 0
        self.log.info(
            "Initialised | balance=%.2f USDT | symbols=%s | restored=%s",
            self.balance, config.SYMBOLS, restored,
        )

    # ── Buffer attachment (called by main.py at startup) ─────────────────────

    def attach_buffer(self, symbol: str, buffer: KlineBuffer) -> None:
        """Attach a pre-seeded KlineBuffer for a symbol. Called before WebSocket starts."""
        self._buffers[symbol] = buffer

    # ── WebSocket candle-close handler ────────────────────────────────────────

    def on_candle_close(self, symbol: str) -> None:
        """
        Called by KlineStreamManager when a candle is confirmed (closed).
        Replaces the timer-based run_once() loop for live trading.
        Subclasses must implement _process_symbol() to consume the buffer.
        """
        if symbol not in self._buffers:
            self.log.warning("on_candle_close fired for unregistered symbol %s — ignoring.", symbol)
            return
        if self.check_daily_loss():
            return
        self.check_stop_loss_take_profit()
        try:
            self._process_symbol(symbol)
        except Exception as exc:
            self.log.error("Error processing %s: %s", symbol, exc, exc_info=True)

    # ── Market data (with retry) ──────────────────────────────────────────────

    def fetch_ohlcv(self, symbol: str, timeframe: str) -> pd.DataFrame:
        """Return OHLCV DataFrame. Retries up to 3 times on network errors."""
        for attempt in range(1, 4):
            try:
                raw = self.exchange.fetch_ohlcv(symbol, timeframe, limit=config.OHLCV_LIMIT)
                df  = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
                df.set_index("timestamp", inplace=True)
                return df.astype(float)
            except ccxt.NetworkError as exc:
                self.log.warning("fetch_ohlcv attempt %d/3 failed: %s", attempt, exc)
                if attempt < 3:
                    time.sleep(5 * attempt)
                else:
                    raise
            except ccxt.ExchangeError:
                raise

    def fetch_ticker(self, symbol: str) -> dict:
        for attempt in range(1, 4):
            try:
                return self.exchange.fetch_ticker(symbol)
            except ccxt.NetworkError as exc:
                self.log.warning("fetch_ticker attempt %d/3 failed: %s", attempt, exc)
                if attempt < 3:
                    time.sleep(5 * attempt)
                else:
                    raise
            except ccxt.ExchangeError:
                raise

    def current_price(self, symbol: str) -> float:
        """
        Return the current price for a symbol.
        Hot path (WebSocket): reads last confirmed close from the KlineBuffer.
        Fallback (warmup / buffer not yet attached): falls back to REST ticker.
        """
        buf = self._buffers.get(symbol)
        if buf is not None and buf.last_price is not None:
            return buf.last_price
        ticker = self.fetch_ticker(symbol)
        return float(ticker["last"])

    # ── Position management (simulated) ──────────────────────────────────────

    def open_position(self, symbol: str, side: str) -> None:
        """
        Simulate opening a position. Deducts commission from balance.
        Enforces MAX_DAILY_TRADES limit.
        """
        if not config.PAPER_TRADING:
            raise RuntimeError("Real trading is disabled.")

        with self._positions_lock:
            if symbol in self.positions:
                self.log.debug("Already in a position for %s — skipping.", symbol)
                return

            if self._day_trade_count >= config.MAX_DAILY_TRADES:
                self.log.info(
                    "Daily trade limit (%d) reached — skipping %s.", config.MAX_DAILY_TRADES, symbol
                )
                return

            price    = self.current_price(symbol)
            notional = self.balance * config.MAX_POSITION_PCT
            size     = notional / price
            comm     = notional * config.COMMISSION_RATE

            self.balance -= comm
            self.positions[symbol] = _new_position(symbol, side, price, size)
            self._day_trade_count += 1

        self.log.info(
            "OPEN %s %s | price=%.4f | size=%.6f | notional=%.2f | commission=%.4f USDT",
            side.upper(), symbol, price, size, notional, comm,
        )
        _save_state(self.name, self.positions, self.closed_trades, self.balance)

    def close_position(self, symbol: str, reason: str = "signal") -> Optional[float]:
        """
        Simulate closing an open position. Deducts commission. Returns net PnL or None.
        """
        with self._positions_lock:
            if symbol not in self.positions:
                return None

            pos   = self.positions.pop(symbol)
            price = self.current_price(symbol)

            gross_pnl = (price - pos["entry_price"]) * pos["size"]  # spot is long-only
            comm      = price * pos["size"] * config.COMMISSION_RATE
            net_pnl   = gross_pnl - comm

            self.balance += net_pnl

        trade = {
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "bot_name":    self.name,
            "symbol":      symbol,
            "side":        pos["side"],
            "entry_price": round(pos["entry_price"], 6),
            "exit_price":  round(price, 6),
            "size":        round(pos["size"], 8),
            "gross_pnl":   round(gross_pnl, 4),
            "commission":  round(comm, 4),
            "net_pnl":     round(net_pnl, 4),
            "reason":      reason,
        }
        with self._trades_lock:
            self.closed_trades.append(trade)

        _append_trade_csv(trade)
        _save_state(self.name, self.positions, self.closed_trades, self.balance)

        self.log.info(
            "CLOSE %s | entry=%.4f exit=%.4f | gross=%.4f comm=%.4f net=%.4f | reason=%s",
            symbol, pos["entry_price"], price, gross_pnl, comm, net_pnl, reason,
        )
        return net_pnl

    # ── Risk management ───────────────────────────────────────────────────────

    def check_stop_loss_take_profit(self) -> None:
        """Check all open positions and close on SL/TP breach."""
        with self._positions_lock:
            symbols = list(self.positions.keys())

        for symbol in symbols:
            with self._positions_lock:
                if symbol not in self.positions:
                    continue
                pos   = self.positions[symbol]
                price = self.current_price(symbol)
                entry = pos["entry_price"]

            pct_change = (price - entry) / entry  # spot: long only

            if pct_change <= -config.STOP_LOSS_PCT:
                self.close_position(symbol, reason="stop_loss")
            elif pct_change >= config.TAKE_PROFIT_PCT:
                self.close_position(symbol, reason="take_profit")

    def check_daily_loss(self) -> bool:
        """
        Reset daily counters at midnight.
        Pause bot for the day if MAX_DAILY_LOSS_PCT is breached.
        Returns True if bot is (or becomes) paused.
        """
        today = date.today()
        if today != self._current_day:
            self._current_day       = today
            self._day_start_balance = self.balance
            self._day_trade_count   = 0
            self.paused             = False
            self.log.info("New trading day — daily counters reset.")

        daily_loss_pct = (self._day_start_balance - self.balance) / self._day_start_balance
        if daily_loss_pct >= config.MAX_DAILY_LOSS_PCT:
            if not self.paused:
                self.log.warning(
                    "Daily loss limit hit (%.2f%%) — bot paused for today.",
                    daily_loss_pct * 100,
                )
                self.paused = True
        return self.paused

    # ── Statistics ────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        with self._trades_lock:
            trades = list(self.closed_trades)

        n         = len(trades)
        wins      = sum(1 for t in trades if t["net_pnl"] > 0)
        win_rate  = (wins / n * 100) if n else 0.0
        total_pnl = sum(t["net_pnl"] for t in trades)
        return_pct = ((self.balance - self.start_balance) / self.start_balance) * 100
        sharpe     = self._sharpe(trades)

        return {
            "bot":        self.name,
            "trades":     n,
            "win_rate":   round(win_rate, 2),
            "total_pnl":  round(total_pnl, 4),
            "sharpe":     round(sharpe, 3),
            "balance":    round(self.balance, 2),
            "return_pct": round(return_pct, 2),
            "paused":     self.paused,
        }

    def _sharpe(self, trades: list) -> float:
        """
        Annualised Sharpe from per-trade net PnL as a fraction of start balance.
        Consistent proxy for live trading where a full equity curve isn't available.
        """
        if len(trades) < 2:
            return 0.0
        returns = [t["net_pnl"] / self.start_balance for t in trades]
        mean    = sum(returns) / len(returns)
        var     = sum((r - mean) ** 2 for r in returns) / len(returns)
        std     = math.sqrt(var)
        if std == 0:
            return 0.0
        return (mean / std) * math.sqrt(252)

    # ── Entry point (subclasses override) ────────────────────────────────────

    def run_once(self) -> None:
        """Called every BOT_LOOP_SECS by main.py. Override in each strategy."""
        raise NotImplementedError
