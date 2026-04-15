"""
KlineStreamManager — pybit WebSocket kline subscriptions for all bots.

One WebSocket session is opened per unique timeframe (pybit groups channels
by session). Incoming candle ticks are routed to the matching KlineBuffer.
Registered callbacks fire only when a candle is confirmed (closed).

pybit SDK handles reconnection internally — no manual retry logic needed.
"""

import logging
import threading
from typing import Callable

from pybit.unified_trading import WebSocket

from kline_buffer import KlineBuffer

log = logging.getLogger("KlineStream")

# ccxt timeframe string → pybit kline interval
# pybit expects an int for minute-based intervals, "D" for daily
PYBIT_INTERVALS: dict[str, int | str] = {
    "1m":  1,
    "3m":  3,
    "5m":  5,
    "15m": 15,
    "30m": 30,
    "1h":  60,
    "2h":  120,
    "4h":  240,
    "1d":  "D",
}


def _bybit_symbol(ccxt_symbol: str) -> str:
    """Convert ccxt symbol to Bybit format: 'BTC/USDT' → 'BTCUSDT'."""
    return ccxt_symbol.replace("/", "").replace("-", "")


class KlineStreamManager:
    """
    Owns all pybit WebSocket connections for live kline data.

    Usage (in main.py):
        mgr = KlineStreamManager()

        # Register one buffer + callback per (symbol, timeframe) pair
        mgr.register(symbol, timeframe, buffer, bot.on_candle_close)

        mgr.start()   # opens WebSocket sessions and subscribes
        ...
        mgr.stop()    # graceful shutdown
    """

    def __init__(self) -> None:
        # (symbol, tf) → KlineBuffer
        self._buffers:   dict[tuple, KlineBuffer] = {}
        # (symbol, tf) → list of callbacks
        self._callbacks: dict[tuple, list[Callable]] = {}
        # tf → pybit WebSocket session
        self._sessions:  dict[str, WebSocket] = {}
        self._lock = threading.Lock()

    # ── Registration (call before start()) ───────────────────────────────────

    def register(
        self,
        symbol:    str,
        timeframe: str,
        buffer:    KlineBuffer,
        callback:  Callable[[str], None],
    ) -> None:
        """
        Register a KlineBuffer and closed-candle callback for one (symbol, timeframe).

        Parameters
        ----------
        symbol    : ccxt-format symbol, e.g. "BTC/USDT"
        timeframe : ccxt-format timeframe, e.g. "5m"
        buffer    : KlineBuffer instance pre-seeded with historical data
        callback  : called as callback(symbol) on every confirmed (closed) candle
        """
        key = (symbol, timeframe)
        with self._lock:
            self._buffers[key] = buffer
            self._callbacks.setdefault(key, []).append(callback)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """
        Open one WebSocket session per unique timeframe and subscribe to all
        registered (symbol, timeframe) pairs.
        Must be called after all register() calls.
        """
        # Group subscriptions by timeframe
        by_tf: dict[str, list[str]] = {}
        for symbol, tf in self._buffers:
            by_tf.setdefault(tf, []).append(symbol)

        for tf, symbols in by_tf.items():
            interval = PYBIT_INTERVALS.get(tf)
            if interval is None:
                log.error("No pybit interval for timeframe '%s' — skipping.", tf)
                continue

            ws = WebSocket(testnet=False, channel_type="spot")
            self._sessions[tf] = ws

            for symbol in symbols:
                bybit_sym = _bybit_symbol(symbol)
                ws.kline_stream(
                    interval=interval,
                    symbol=bybit_sym,
                    callback=lambda msg, s=symbol, t=tf: self._on_message(msg, s, t),
                )
                log.info("Subscribed  kline.%s.%s", interval, bybit_sym)

    def stop(self) -> None:
        """Gracefully close all WebSocket sessions."""
        for tf, ws in self._sessions.items():
            try:
                ws.exit()
                log.info("WebSocket tf=%s closed.", tf)
            except Exception as exc:
                log.warning("Error closing WebSocket tf=%s: %s", tf, exc)
        self._sessions.clear()

    # ── Internal message routing ──────────────────────────────────────────────

    def _on_message(self, msg: dict, symbol: str, tf: str) -> None:
        """
        pybit calls this on every kline tick for the subscribed (symbol, tf).

        Updates the buffer unconditionally.
        Fires registered callbacks only when the candle is confirmed (closed).
        """
        if "data" not in msg:
            return  # heartbeat or subscription-ack

        key = (symbol, tf)
        buf = self._buffers.get(key)
        if buf is None:
            log.warning("No buffer for %s %s — dropping message.", symbol, tf)
            return

        for candle in msg["data"]:
            confirmed = buf.update(candle)
            if confirmed:
                for cb in self._callbacks.get(key, []):
                    try:
                        cb(symbol)
                    except Exception as exc:
                        log.error(
                            "Callback error for %s %s: %s", symbol, tf, exc, exc_info=True
                        )
