"""
KlineStreamManager — kline subscriptions via websocket.create_connection.

A single background thread calls ws.recv() in a tight loop. Any exception
triggers a 5-second back-off and a full reconnect. A watchdog thread
force-closes the socket if no kline data arrives for _MAX_SILENCE_SECS,
which causes recv() to raise and the recv loop to reconnect.
"""

import json
import logging
import threading
import time
from typing import Callable, Optional

import websocket

from kline_buffer import KlineBuffer

log = logging.getLogger("KlineStream")

_WS_URL           = "wss://stream.bybit.com/v5/public/spot"
_RECV_TIMEOUT     = 30    # recv() unblocks at most every N seconds (for pings)
_PING_INTERVAL    = 20    # send {"op":"ping"} every N seconds
_MAX_SILENCE_SECS = 180   # watchdog force-reconnect threshold
_RAW_LOG_COUNT    = 5     # log this many raw messages at DEBUG on startup
_RECONNECT_DELAY  = 5     # seconds to wait before reconnecting after error

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

        self._ws:           Optional[websocket.WebSocket] = None  # current live socket
        self._recv_thread:  Optional[threading.Thread]    = None
        self._watchdog:     Optional[threading.Thread]    = None

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
            log.error("No interval mapping for '%s' — skipping.", timeframe)
            return
        topic = f"kline.{interval}.{_bybit_symbol(symbol)}"
        self._topic_to_key[topic] = key

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._recv_thread = threading.Thread(
            target=self._recv_loop, name="ws-recv", daemon=True
        )
        self._recv_thread.start()

        self._watchdog = threading.Thread(
            target=self._watchdog_loop, name="ws-watchdog", daemon=True
        )
        self._watchdog.start()

        log.info(
            "KlineStreamManager started — %d topic(s), silence threshold %ds.",
            len(self._topic_to_key), _MAX_SILENCE_SECS,
        )

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        if self._recv_thread and self._recv_thread.is_alive():
            self._recv_thread.join(timeout=10)
        log.info("KlineStreamManager stopped.")

    # ── Recv loop (the core) ──────────────────────────────────────────────────

    def _recv_loop(self) -> None:
        """
        Connect → subscribe → recv forever.
        Any exception triggers a _RECONNECT_DELAY back-off and full reconnect.
        """
        args    = list(self._topic_to_key.keys())
        sub_msg = json.dumps({"op": "subscribe", "args": args})
        ping    = json.dumps({"op": "ping"})

        while not self._stop_event.is_set():
            ws: Optional[websocket.WebSocket] = None
            try:
                log.info("Connecting to %s …", _WS_URL)
                ws = websocket.create_connection(_WS_URL, timeout=_RECV_TIMEOUT)

                with self._lock:
                    self._ws = ws
                    self._last_msg = time.monotonic()

                log.info("Connected. Subscribing: %s", args)
                ws.send(sub_msg)

                last_ping = time.monotonic()

                while not self._stop_event.is_set():
                    try:
                        raw = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        # Normal — use the timeout to drive periodic pings
                        now = time.monotonic()
                        if now - last_ping >= _PING_INTERVAL:
                            ws.send(ping)
                            last_ping = now
                            log.debug("Ping sent.")
                        continue

                    if not raw:
                        continue

                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        log.error("JSON parse error: %s | raw=%s", exc, raw[:200])
                        continue

                    self._on_message(msg)

                    now = time.monotonic()
                    if now - last_ping >= _PING_INTERVAL:
                        ws.send(ping)
                        last_ping = now
                        log.debug("Ping sent.")

            except Exception as exc:
                log.error(
                    "WebSocket error: %s — reconnecting in %ds.",
                    exc, _RECONNECT_DELAY,
                )
            finally:
                with self._lock:
                    self._ws = None
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass

            if not self._stop_event.is_set():
                self._stop_event.wait(_RECONNECT_DELAY)

    # ── Watchdog ──────────────────────────────────────────────────────────────

    def _watchdog_loop(self) -> None:
        """
        Wake every 60 s. If no kline data for _MAX_SILENCE_SECS, force-close
        the socket so recv() raises and the recv loop reconnects.
        """
        while not self._stop_event.wait(60):
            with self._lock:
                last = self._last_msg
                ws   = self._ws
            if last == 0.0:
                continue  # not yet connected
            silence = time.monotonic() - last
            if silence > _MAX_SILENCE_SECS:
                log.warning(
                    "No kline data for %.0fs — force-closing socket to trigger reconnect.",
                    silence,
                )
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass

    # ── Message handling ──────────────────────────────────────────────────────

    def _on_message(self, msg: dict) -> None:
        # Log first N messages at DEBUG
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

        # Non-kline messages: subscribe-ack, pong, heartbeat
        if not topic.startswith("kline.") or "data" not in msg:
            log.debug(
                "Non-kline: op=%s success=%s ret_msg=%s",
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
