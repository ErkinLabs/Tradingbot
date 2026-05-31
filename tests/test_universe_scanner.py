"""Tests for dynamic universe selection (mocked exchange)."""

import unittest
from unittest.mock import MagicMock, patch

import config
from universe_scanner import UniverseManager


def _ticker(symbol, qv, pct=5.0, last=100.0):
    return {
        "symbol": symbol,
        "quoteVolume": qv,
        "percentage": pct,
        "last": last,
    }


class TestUniverseScanner(unittest.TestCase):

    def _make_mgr(self, tickers: dict, bots=None):
        exchange = MagicMock()
        exchange.fetch_tickers.return_value = tickers
        exchange.fetch_ticker.side_effect = lambda s: tickers.get(s, {})
        exchange.fetch_ohlcv.return_value = [
            [1, 100, 101, 99, 100, 1000],
        ] * 30
        bots = bots or []
        return UniverseManager(exchange, lambda: bots)

    def test_daily_whitelist_filters_by_volume(self):
        tickers = {
            "BTC/USDT": _ticker("BTC/USDT", 50_000_000),
            "DOGE/USDT": _ticker("DOGE/USDT", 100_000),
            "ETH/USDT": _ticker("ETH/USDT", 20_000_000),
        }
        mgr = self._make_mgr(tickers)
        wl = mgr._build_daily_whitelist()
        self.assertIn("BTC/USDT", wl)
        self.assertIn("ETH/USDT", wl)
        self.assertNotIn("DOGE/USDT", wl)

    def test_rescan_pins_open_positions(self):
        bot = MagicMock()
        bot.positions = {"RENDER/USDT": {"side": "long"}}
        bot._positions_lock = __import__("threading").Lock()

        tickers = {
            "BTC/USDT": _ticker("BTC/USDT", 50_000_000),
            "ETH/USDT": _ticker("ETH/USDT", 40_000_000),
            "SOL/USDT": _ticker("SOL/USDT", 30_000_000),
            "BNB/USDT": _ticker("BNB/USDT", 25_000_000),
        }
        mgr = self._make_mgr(tickers, bots=[bot])

        with patch.object(mgr, "_build_4h_universe", return_value=(["BTC/USDT"], {"BTC/USDT": 1.0})):
            symbols = mgr.rescan(force_daily=True)

        self.assertIn("RENDER/USDT", symbols)
        self.assertIn("BTC/USDT", symbols)

    def test_fallback_when_no_tickers(self):
        mgr = self._make_mgr({})
        wl = mgr._build_daily_whitelist()
        self.assertEqual(wl, list(config.FALLBACK_SYMBOLS))


if __name__ == "__main__":
    unittest.main()
