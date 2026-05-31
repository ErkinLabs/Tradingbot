"""
Dynamic universe selection — rank Bybit spot USDT pairs by activity.

Schedule:
  - Daily (UTC midnight): build whitelist of top N by 24h quote volume
  - Every UNIVERSE_RESCAN_HOURS: score 4h ATR / change / relative volume → pick top K
  - Final universe = 4h picks ∩ daily whitelist (+ pinned open-position symbols)
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

import pandas as pd
import pandas_ta as ta

import config

log = logging.getLogger("UniverseScanner")


class UniverseManager:
    """Maintains the active trading symbol list with periodic rescans."""

    def __init__(
        self,
        exchange,
        bots_ref: Callable[[], list],
    ) -> None:
        self.exchange = exchange
        self._bots_ref = bots_ref
        self._lock = threading.Lock()
        self._active: list[str] = list(config.FALLBACK_SYMBOLS)
        self._daily_whitelist: list[str] = list(config.FALLBACK_SYMBOLS)
        self._last_daily_scan: float = 0.0
        self._last_4h_scan: float = 0.0
        self._last_scores: dict[str, float] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def active_symbols(self) -> list[str]:
        with self._lock:
            return list(self._active)

    @property
    def daily_whitelist(self) -> list[str]:
        with self._lock:
            return list(self._daily_whitelist)

    @property
    def last_scores(self) -> dict[str, float]:
        with self._lock:
            return dict(self._last_scores)

    def get_symbols(self) -> list[str]:
        """Active universe plus any symbol with an open position."""
        pinned = self._pinned_symbols()
        with self._lock:
            merged = sorted(set(self._active) | pinned)
        return merged

    def should_rescan(self) -> bool:
        with self._lock:
            return time.time() - self._last_4h_scan >= config.UNIVERSE_RESCAN_HOURS * 3600

    def rescan(self, force_daily: bool = False) -> list[str]:
        """Run scanner and update active universe. Returns new symbol list."""
        now = time.time()
        need_daily = force_daily or (now - self._last_daily_scan >= 86400)

        if need_daily:
            whitelist = self._build_daily_whitelist()
            with self._lock:
                self._daily_whitelist = whitelist
                self._last_daily_scan = now
            log.info("Daily universe whitelist (%d): %s", len(whitelist), whitelist)

        with self._lock:
            whitelist = list(self._daily_whitelist)

        active, scores = self._build_4h_universe(whitelist)
        pinned = self._pinned_symbols()
        merged = sorted(set(active) | pinned)

        with self._lock:
            self._active = active
            self._last_scores = scores
            self._last_4h_scan = now

        log.info(
            "Universe updated (%d active, %d pinned): %s",
            len(active), len(pinned), merged,
        )
        return merged

    def initial_scan(self) -> list[str]:
        return self.rescan(force_daily=True)

    # ── Scanner internals ─────────────────────────────────────────────────────

    def _pinned_symbols(self) -> set[str]:
        symbols: set[str] = set()
        for bot in self._bots_ref():
            with bot._positions_lock:
                symbols.update(bot.positions.keys())
        return symbols

    def _usdt_spot_candidates(self) -> list[dict]:
        tickers = self.exchange.fetch_tickers()
        out: list[dict] = []

        for symbol, t in tickers.items():
            if not symbol.endswith("/USDT"):
                continue
            if t.get("quoteVolume") is None:
                continue
            qv = float(t["quoteVolume"] or 0)
            if qv < config.UNIVERSE_MIN_QUOTE_VOLUME_USDT:
                continue
            base = symbol.split("/")[0]
            if base in config.UNIVERSE_EXCLUDE_BASES:
                continue
            pct = abs(float(t.get("percentage") or 0))
            out.append({
                "symbol": symbol,
                "quote_volume": qv,
                "change_pct": pct,
                "last": float(t.get("last") or 0),
            })

        out.sort(key=lambda x: x["quote_volume"], reverse=True)
        return out[: config.UNIVERSE_CANDIDATE_POOL]

    def _build_daily_whitelist(self) -> list[str]:
        candidates = self._usdt_spot_candidates()
        if not candidates:
            return list(config.FALLBACK_SYMBOLS)

        scored = sorted(
            candidates,
            key=lambda c: (
                c["change_pct"] * 0.55
                + min(c["quote_volume"] / 10_000_000, 10.0) * 0.45
            ),
            reverse=True,
        )
        wl = [c["symbol"] for c in scored[: config.UNIVERSE_DAILY_TOP_N]]
        return wl or list(config.FALLBACK_SYMBOLS)

    def _atr_pct(self, symbol: str, timeframe: str, limit: int = 30) -> Optional[float]:
        try:
            raw = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            if not raw or len(raw) < 15:
                return None
            df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
            atr = ta.atr(df["high"], df["low"], df["close"], length=14)
            if atr is None or atr.empty or pd.isna(atr.iloc[-1]):
                return None
            close = float(df["close"].iloc[-1])
            if close <= 0:
                return None
            return float(atr.iloc[-1]) / close
        except Exception as exc:
            log.debug("ATR fetch failed for %s: %s", symbol, exc)
            return None

    def _build_4h_universe(self, whitelist: list[str]) -> tuple[list[str], dict[str, float]]:
        scores: dict[str, float] = {}
        scan_list = whitelist[: config.UNIVERSE_4H_SCAN_TOP]

        for symbol in scan_list:
            atr_pct = self._atr_pct(symbol, config.UNIVERSE_SCANNER_TF)
            if atr_pct is None:
                continue
            try:
                t = self.exchange.fetch_ticker(symbol)
                change = abs(float(t.get("percentage") or 0))
                qv     = float(t.get("quoteVolume") or 0)
            except Exception:
                continue

            rel_vol = min(qv / max(config.UNIVERSE_MIN_QUOTE_VOLUME_USDT, 1), 5.0)
            score = (
                config.UNIVERSE_W_ATR * atr_pct * 100
                + config.UNIVERSE_W_CHANGE * change
                + config.UNIVERSE_W_VOLUME * rel_vol
            )
            scores[symbol] = round(score, 4)

        if not scores:
            fallback = whitelist[: config.UNIVERSE_ACTIVE_COUNT] or list(config.FALLBACK_SYMBOLS)
            return fallback, {}

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        active = [sym for sym, _ in ranked[: config.UNIVERSE_ACTIVE_COUNT]]
        return active, dict(ranked)
