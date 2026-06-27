"""
data_loader.py — Historical OHLCV fetcher with parquet cache.

Usage:
    loader = DataLoader()
    df = loader.fetch_range("BTC/USDT", "5m", "2024-01-01", "2024-12-31")
"""

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import ccxt
import pandas as pd
from rich.progress import (
    BarColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

# Place cache alongside this file's parent (backtest/cache/)
CACHE_DIR = Path(__file__).parent / "cache"

_TF_SECONDS: dict[str, int] = {
    "1m":  60,
    "3m":  180,
    "5m":  300,
    "15m": 900,
    "30m": 1_800,
    "1h":  3_600,
    "2h":  7_200,
    "4h":  14_400,
    "1d":  86_400,
}

_FETCH_LIMIT = 1_000   # candles per request (Bybit supports up to 1 000)
_REQUEST_DELAY = 0.12  # seconds between requests (rate-limit courtesy)


def _tf_ms(timeframe: str) -> int:
    return _TF_SECONDS[timeframe] * 1_000


def _cache_path(symbol: str, timeframe: str, start: str, end: str) -> Path:
    safe = symbol.replace("/", "_")
    return CACHE_DIR / f"{safe}_{timeframe}_{start}_{end}.parquet"


def _covers_range(cached_df: pd.DataFrame, start_ms: int, end_ms: int) -> bool:
    """True if cached DataFrame covers [start_ms, end_ms] without gaps."""
    if cached_df.empty:
        return False
    idx_ms = cached_df.index.astype("int64") // 1_000_000  # ns → ms
    return int(idx_ms[0]) <= start_ms and int(idx_ms[-1]) >= end_ms - _tf_ms("1d")


class DataLoader:
    """Fetches and caches OHLCV data from Bybit (public API)."""

    def __init__(self) -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.exchange = ccxt.bybit({"defaultType": "linear"})
        self.exchange.load_markets()

    # ── Public API ─────────────────────────────────────────────────────────────

    def fetch_range(
        self,
        symbol: str,
        timeframe: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """
        Return OHLCV DataFrame for the given symbol/timeframe/date range.
        Loads from parquet cache if available; otherwise downloads from Bybit.

        Parameters
        ----------
        symbol     : e.g. "BTC/USDT"
        timeframe  : e.g. "5m", "15m", "1h"
        start_date : "YYYY-MM-DD" (inclusive, UTC)
        end_date   : "YYYY-MM-DD" (inclusive, UTC — data up to end-of-day)
        """
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt   = datetime.strptime(end_date,   "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc
        )
        since_ms = int(start_dt.timestamp() * 1_000)
        until_ms = int(end_dt.timestamp()   * 1_000)

        cache_file = _cache_path(symbol, timeframe, start_date, end_date)

        # ── Cache hit ──────────────────────────────────────────────────────────
        if cache_file.exists():
            df = pd.read_parquet(cache_file)
            if _covers_range(df, since_ms, until_ms):
                print(f"  [cache] {symbol} {timeframe} loaded from {cache_file.name} ({len(df):,} bars)")
                return df.loc[start_date:end_date]

        # ── Download ───────────────────────────────────────────────────────────
        df = self._download(symbol, timeframe, since_ms, until_ms)

        # Save to cache
        df.to_parquet(cache_file)
        print(f"  [cache] Saved {len(df):,} bars -> {cache_file.name}")

        return df.loc[start_date:end_date]

    # ── Internal ───────────────────────────────────────────────────────────────

    def _download(
        self, symbol: str, timeframe: str, since_ms: int, until_ms: int
    ) -> pd.DataFrame:
        tf_ms = _tf_ms(timeframe)
        total_candles = max(1, (until_ms - since_ms) // tf_ms)

        rows: list[list] = []
        current_since = since_ms

        with Progress(
            TextColumn(f"  [cyan]Downloading[/cyan] {symbol} {timeframe}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("({task.completed}/{task.total} bars)"),
            TimeRemainingColumn(),
            transient=True,
        ) as progress:
            task = progress.add_task("", total=total_candles)

            while current_since <= until_ms:
                try:
                    batch = self.exchange.fetch_ohlcv(
                        symbol, timeframe,
                        since=current_since,
                        limit=_FETCH_LIMIT,
                    )
                except ccxt.NetworkError as exc:
                    print(f"\n  Network error: {exc} — retrying in 10 s…", file=sys.stderr)
                    time.sleep(10)
                    continue
                except ccxt.ExchangeError as exc:
                    print(f"\n  Exchange error: {exc} — aborting.", file=sys.stderr)
                    break

                if not batch:
                    break

                # Drop any candles beyond our requested end
                batch = [b for b in batch if b[0] <= until_ms]
                if not batch:
                    break

                rows.extend(batch)
                progress.advance(task, len(batch))

                last_ts = batch[-1][0]
                if last_ts >= until_ms or len(batch) < _FETCH_LIMIT:
                    break

                current_since = last_ts + tf_ms
                time.sleep(_REQUEST_DELAY)

        if not rows:
            raise ValueError(f"No data returned for {symbol} {timeframe}")

        df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)
        df = df.astype(float)
        df = df[~df.index.duplicated(keep="first")]
        df.sort_index(inplace=True)

        print(f"  Downloaded {len(df):,} bars for {symbol} {timeframe}")
        return df
