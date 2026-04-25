"""
KlineStreamManager — raw WebSocket kline subscriptions for all bots.

Connects directly to wss://stream.bybit.com/v5/public/spot using
websocket-client and subscribes to kline topics for all registered
(symbol, timeframe) pairs. A background watchdog reconnects if the
session goes silent for longer than _MAX_SILENCE_SECS.
"""

import json
import logging
import threading
import time
from typing import Callable, Optional

import websocket

from kline_buffer import KlineBuffer

log = logging.getLogger("KlineStream")

_WS_URL = "wss://stream.bybit.com/v5/public/spot"

# ccxt timeframe string → Bybit V5 kline interval string
TF_TO_INTERVAL: dict[str, str] = {
    "1m":  "1",
    "3m":  "3",
    "5m":  "5",
    "15m": "15",
    "30m": "30",
    "1h":  "60",
    "2h":  "120",
    "4h":  "240",
    "1d":  "D",
}

_MAX_SILENCE_SECS  = 180
_RAW_LOG_COUNT     = 5
_PING_INTERVAL_SECS = 20


def _bybit_symbol(ccxt_symbol: str) -> str:
    return ccxt_symbol.replace("/", "").replace("-", "")


class KlineStreamManager:
    """
    Owns a single Bybit V5 WebSocket connection for live kline data.

    Usage (in main.py):
        mgr = KlineStreamManager()
        mgr.register(symbol, timeframe, buffer, bot.on_candle_close)
        mgr.start()
        ...
        mgr.stop()
    """

    def __init__(self) -> None:
        self._buffers:      dict[tuple, KlineBuffer]    = {}
        self._callbacks:    dict[tuple, list[Callable]] = {}
        self._topic_to_key: dict[str, tuple]            = {}  # "kline.5.BTCUSDT" → (ccxt_sym, tf)

        self._ws:           Optional[websocket.WebSocketApp] = None
        self._ws_thread:    Optional[threading.Thread]       = None
        self._watchdog:     Optional[threading.Thread]       = None
        self._ping_thread:  Optional[threading.Thread]       = None

        self._last_msg:     float = 0.0   # monotonic time of last kline data message
        self._lock          = threading.Lock()
        self._stop_event    = threading.Event()
        self._raw_logged:   int = 0

    # ── Registration ──────────────────────────────────────────────────────────

    def register(
        self,
        symbol:    str,
        timeframe: str,
        buffer:    KlineBuffer,
        callback:  Callable[[str], None],
    ) -> None:
        """Register a (symbol, timeframe) subscription. Call before start()."""
        key = (symbol, timeframe)
        with self._lock:
            self._buffers[key] = buffer
            self._callbacks.setdefault(key, []).append(callback)

        interval = TF_TO_INTERVAL.get(timeframe)
        if interval is None:
            log.error("No interval mapping for timeframe '%s' — skipping.", timeframe)
            return
        topic = f"kline.{interval}.{_bybit_symbol(symbol)}"
        self._topic_to_key[topic] = key

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._open_connection()

        self._watchdog = threading.Thread(
            target=self._watchdog_loop, name="ws-watchdog", daemon=True
        )
        self._watchdog.start()

        self._ping_thread = threading.Thread(
            target=self._ping_loop, name="ws-ping", daemon=True
        )
        self._ping_thread.start()

        log.info(
            "KlineStreamManager started — %d topic(s), reconnect threshold %ds.",
            len(self._topic_to_key), _MAX_SILENCE_SECS,
        )

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception as exc:
                log.warning("Error closing WebSocket: %s", exc)
        if self._ws_thread and self._ws_thread.is_alive():
            self._ws_thread.join(timeout=5)
        log.info("KlineStreamManager stopped.")

    # ── Connection management ─────────────────────────────────────────────────

    def _open_connection(self) -> None:
        args         = list(self._topic_to_key.keys())
        sub_msg      = json.dumps({"op": "subscribe", "args": args})

        def on_open(ws: websocket.WebSocketApp) -> None:
            log.info("WebSocket connected — subscribing to %d topic(s): %s", len(args), args)
            ws.send(sub_msg)
            with self._lock:
                self._last_msg = time.monotonic()

        def on_message(ws: websocket.WebSocketApp, raw: str) -> None:
            try:
                self._on_message(json.loads(raw))
            except Exception as exc:
                log.error("Message parse error: %s | raw=%s", exc, raw[:200])

        def on_error(ws: websocket.WebSocketApp, error: Exception) -> None:
            log.error("WebSocket error: %s", error)

        def on_close(
            ws: websocket.WebSocketApp,
            code: Optional[int],
            msg:  Optional[str],
        ) -> None:
            log.warning("WebSocket closed (code=%s msg=%s).", code, msg)

        ws = websocket.WebSocketApp(
            _WS_URL,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        with self._lock:
            self._ws = ws

        t = threading.Thread(target=ws.run_forever, name="ws-main", daemon=True)
        t.start()
        self._ws_thread = t

    def _reconnect(self) -> None:
        log.warning("Reconnecting WebSocket…")

        with self._lock:
            old_ws = self._ws
            self._ws = None

        if old_ws is not None:
            try:
                old_ws.close()
            except Exception:
                pass

        if self._ws_thread and self._ws_thread.is_alive():
            self._ws_thread.join(timeout=5)

        try:
            self._open_connection()
            log.info("WebSocket reconnected successfully.")
        except Exception as exc:
            log.error("Reconnect failed: %s", exc)

    # ── Background threads ────────────────────────────────────────────────────

    def _watchdog_loop(self) -> None:
        """Wake every 60 s; reconnect if no data message received recently."""
        while not self._stop_event.wait(60):
            with self._lock:
                last = self._last_msg
            if last == 0.0:
                continue  # connection not established yet
            silence = time.monotonic() - last
            if silence > _MAX_SILENCE_SECS:
                log.warning("No data for %.0fs — triggering reconnect.", silence)
                self._reconnect()

    def _ping_loop(self) -> None:
        """Send Bybit ping every 20 s to keep the connection alive."""
        ping = json.dumps({"op": "ping"})
        while not self._stop_event.wait(_PING_INTERVAL_SECS):
            with self._lock:
                ws = self._ws
            if ws is not None:
                try:
                    ws.send(ping)
                    log.debug("Ping sent.")
                except Exception as exc:
                    log.debug("Ping failed: %s", exc)

    # ── Message routing ───────────────────────────────────────────────────────

    def _on_message(self, msg: dict) -> None:
        # Log the first N messages at DEBUG to surface format issues
        with self._lock:
            raw_idx = self._raw_logged
            if raw_idx < _RAW_LOG_COUNT:
                self._raw_logged += 1
        if raw_idx < _RAW_LOG_COUNT:
            try:
                snippet = json.dumps(msg)[:600]
            except Exception:
                snippet = str(msg)[:600]
            log.debug("RAW[%d] %s", raw_idx + 1, snippet)

        topic = msg.get("topic", "")

        # Non-kline messages: ack, pong, heartbeat
        if not topic.startswith("kline.") or "data" not in msg:
            log.debug(
                "Non-kline msg: op=%s success=%s ret_msg=%s",
                msg.get("op"), msg.get("success"), msg.get("ret_msg"),
            )
            return

        with self._lock:
            self._last_msg = time.monotonic()

        key = self._topic_to_key.get(topic)
        if key is None:
            log.warning("Unregistered topic '%s' — dropping.", topic)
            return

        symbol, tf = key
        buf = self._buffers.get(key)
        if buf is None:
            log.warning("No buffer for %s %s — dropping.", symbol, tf)
            return

        for candle in msg["data"]:
            log.debug(
                "Tick %s %s | close=%s confirm=%s",
                symbol, tf, candle.get("close"), candle.get("confirm"),
            )
            confirmed = buf.update(candle)

            if confirmed:
                log.info(
                    "Candle CLOSED %s %s close=%s — firing %d callback(s)",
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
