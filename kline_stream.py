"""
KlineStreamManager — pybit WebSocket kline subscriptions for all bots.

One WebSocket session is opened per unique timeframe. A background watchdog
thread monitors message activity and triggers an automatic reconnect if a
session goes silent for longer than _MAX_SILENCE_SECS (e.g. after a
ping/pong timeout that pybit did not self-heal).
"""

import json
import logging
import threading
import time
from typing import Callable, Optional

from pybit.unified_trading import WebSocket

from kline_buffer import KlineBuffer

log = logging.getLogger("KlineStream")

# ccxt timeframe string → pybit kline interval
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

# Reconnect if no data message received for this many seconds
_MAX_SILENCE_SECS = 180

# Log the first N raw messages at INFO to surface format issues immediately
_RAW_LOG_COUNT = 10


def _bybit_symbol(ccxt_symbol: str) -> str:
    """Convert ccxt symbol to Bybit format: 'BTC/USDT' → 'BTCUSDT'."""
    return ccxt_symbol.replace("/", "").replace("-", "")


class KlineStreamManager:
    """
    Owns all pybit WebSocket connections for live kline data.

    Usage (in main.py):
        mgr = KlineStreamManager()
        mgr.register(symbol, timeframe, buffer, bot.on_candle_close)
        mgr.start()   # opens sessions + starts watchdog
        ...
        mgr.stop()    # graceful shutdown
    """

    def __init__(self) -> None:
        self._buffers:   dict[tuple, KlineBuffer]   = {}
        self._callbacks: dict[tuple, list[Callable]] = {}
        self._sessions:  dict[str, WebSocket]        = {}
        self._last_msg:  dict[str, float]            = {}  # tf → monotonic timestamp
        self._lock       = threading.Lock()
        self._stop_event = threading.Event()
        self._watchdog:  Optional[threading.Thread]  = None
        self._raw_logged: int = 0  # how many raw messages logged so far (all TFs combined)

    # ── Registration ──────────────────────────────────────────────────────────

    def register(
        self,
        symbol:    str,
        timeframe: str,
        buffer:    KlineBuffer,
        callback:  Callable[[str], None],
    ) -> None:
        key = (symbol, timeframe)
        with self._lock:
            self._buffers[key] = buffer
            self._callbacks.setdefault(key, []).append(callback)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        by_tf: dict[str, list[str]] = {}
        for symbol, tf in self._buffers:
            by_tf.setdefault(tf, []).append(symbol)

        for tf, symbols in by_tf.items():
            self._open_session(tf, symbols)

        self._watchdog = threading.Thread(
            target=self._watchdog_loop, name="ws-watchdog", daemon=True
        )
        self._watchdog.start()
        log.info("WebSocket watchdog started (reconnect threshold: %ds).", _MAX_SILENCE_SECS)

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            sessions = dict(self._sessions)
        for tf, ws in sessions.items():
            try:
                ws.exit()
                log.info("WebSocket tf=%s closed.", tf)
            except Exception as exc:
                log.warning("Error closing WebSocket tf=%s: %s", tf, exc)
        with self._lock:
            self._sessions.clear()

    # ── Session management ────────────────────────────────────────────────────

    def _open_session(self, tf: str, symbols: list[str]) -> None:
        interval = PYBIT_INTERVALS.get(tf)
        if interval is None:
            log.error("No pybit interval for timeframe '%s' — skipping.", tf)
            return

        ws = WebSocket(testnet=False, channel_type="spot")
        for symbol in symbols:
            bybit_sym = _bybit_symbol(symbol)
            ws.kline_stream(
                interval=interval,
                symbol=bybit_sym,
                callback=lambda msg, s=symbol, t=tf: self._on_message(msg, s, t),
            )
            log.info("Subscribed kline.%s.%s", interval, bybit_sym)

        with self._lock:
            self._sessions[tf] = ws
            self._last_msg[tf] = time.monotonic()

    def _reconnect_tf(self, tf: str) -> None:
        log.warning("Reconnecting WebSocket tf=%s …", tf)

        with self._lock:
            old_ws = self._sessions.pop(tf, None)

        if old_ws is not None:
            try:
                old_ws.exit()
            except Exception:
                pass

        symbols = [s for (s, t) in list(self._buffers.keys()) if t == tf]
        try:
            self._open_session(tf, symbols)
            log.info("WebSocket tf=%s reconnected successfully.", tf)
        except Exception as exc:
            log.error("Reconnect failed for tf=%s: %s", tf, exc)

    # ── Watchdog ──────────────────────────────────────────────────────────────

    def _watchdog_loop(self) -> None:
        """Wake every 60 s; reconnect any session that has been silent too long."""
        while not self._stop_event.wait(60):
            now = time.monotonic()
            with self._lock:
                dead = [
                    tf for tf, t in self._last_msg.items()
                    if now - t > _MAX_SILENCE_SECS
                ]
            for tf in dead:
                log.warning(
                    "WebSocket tf=%s silent for >%ds — triggering reconnect.",
                    tf, _MAX_SILENCE_SECS,
                )
                self._reconnect_tf(tf)

    # ── Message routing ───────────────────────────────────────────────────────

    def _on_message(self, msg: dict, symbol: str, tf: str) -> None:
        """Route a pybit kline tick to the matching buffer; fire callbacks on close."""

        # ── Raw message logging (first _RAW_LOG_COUNT messages, all formats) ──
        with self._lock:
            raw_idx = self._raw_logged
            if raw_idx < _RAW_LOG_COUNT:
                self._raw_logged += 1
        if raw_idx < _RAW_LOG_COUNT:
            try:
                raw_str = json.dumps(msg)[:600]
            except Exception:
                raw_str = str(msg)[:600]
            log.info("RAW[%d] %s tf=%s | %s", raw_idx + 1, symbol, tf, raw_str)

        # ── Non-data messages (subscription-ack, heartbeat, etc.) ─────────────
        if "data" not in msg:
            log.debug(
                "Non-data msg %s tf=%s | op=%s success=%s ret_msg=%s keys=%s",
                symbol, tf,
                msg.get("op"), msg.get("success"), msg.get("ret_msg"),
                list(msg.keys()),
            )
            return

        with self._lock:
            self._last_msg[tf] = time.monotonic()

        key = (symbol, tf)
        buf = self._buffers.get(key)
        if buf is None:
            log.warning("No buffer for %s %s — dropping message.", symbol, tf)
            return

        for candle in msg["data"]:
            # Log field names once per session to verify the confirm field name
            if raw_idx < _RAW_LOG_COUNT:
                log.info(
                    "CANDLE FIELDS %s tf=%s | keys=%s | confirm=%s",
                    symbol, tf, list(candle.keys()), candle.get("confirm"),
                )

            # Handle both "confirm" (Bybit V5 standard) and legacy "confirmed"
            confirm_raw = candle.get("confirm", candle.get("confirmed", False))
            confirmed   = bool(confirm_raw)

            log.debug(
                "Tick %s tf=%s | close=%s confirm=%s(%s)",
                symbol, tf, candle.get("close"), confirm_raw, confirmed,
            )

            buf.update_confirm_aware(candle, confirmed)

            if confirmed:
                log.info(
                    "Candle CLOSED %s tf=%s close=%s — firing %d callback(s)",
                    symbol, tf, candle.get("close"),
                    len(self._callbacks.get(key, [])),
                )
                for cb in self._callbacks.get(key, []):
                    try:
                        cb(symbol)
                    except Exception as exc:
                        log.error(
                            "Callback error %s %s: %s", symbol, tf, exc, exc_info=True
                        )
