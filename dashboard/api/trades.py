"""Trades, stats and signal endpoints."""

from __future__ import annotations

import csv
import math
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import ccxt
import pandas as pd
from fastapi import APIRouter, Query

import config

router = APIRouter()


# ── CSV helpers ───────────────────────────────────────────────────────────────

def _read_csv(bot: Optional[str] = None) -> list[dict]:
    path = Path(config.TRADE_LOG_FILE)
    if not path.exists():
        return []
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if bot and row.get("bot_name") != bot:
                continue
            rows.append(dict(row))
    return rows


def _sharpe(net_pnls: list[float], start_bal: float) -> float:
    if len(net_pnls) < 2 or start_bal == 0:
        return 0.0
    rets = [p / start_bal for p in net_pnls]
    mean = sum(rets) / len(rets)
    var  = sum((r - mean) ** 2 for r in rets) / len(rets)
    std  = math.sqrt(var)
    return round((mean / std) * math.sqrt(252), 3) if std > 0 else 0.0


def _stats_from_rows(rows: list[dict], bot_name: str) -> dict:
    n         = len(rows)
    pnls      = [float(r.get("net_pnl", 0)) for r in rows]
    wins      = sum(1 for p in pnls if p > 0)
    total_pnl = sum(pnls)
    win_rate  = (wins / n * 100) if n else 0.0
    start_bal = config.INITIAL_BALANCE * config.BOT_ALLOCATIONS.get(bot_name, 0.33)
    balance   = start_bal + total_pnl
    ret_pct   = ((balance - start_bal) / start_bal * 100) if start_bal else 0.0
    return {
        "bot":            bot_name,
        "trades":         n,
        "win_rate":       round(win_rate, 2),
        "total_pnl":      round(total_pnl, 4),
        "sharpe":         _sharpe(pnls, start_bal),
        "balance":        round(balance, 2),
        "return_pct":     round(ret_pct, 2),
        "paused":         False,
        "open_positions": [],
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/trades")
async def get_trades(bot: str = "all"):
    rows = _read_csv(bot if bot != "all" else None)
    return rows


@router.get("/stats")
async def get_stats():
    from dashboard.server import get_live_bots

    bots = get_live_bots()
    if bots:
        result = []
        for b in bots:
            s = b.get_stats()
            s["open_positions"] = [
                {
                    "symbol":      sym,
                    "side":        pos["side"],
                    "entry_price": pos["entry_price"],
                    "size":        pos["size"],
                    "opened_at":   pos["opened_at"].isoformat(),
                }
                for sym, pos in b.positions.items()
            ]
            result.append(s)
        return result

    # Standalone — compute from CSV
    result = []
    for name in ["MACD", "RSI_VWAP", "CVD"]:
        result.append(_stats_from_rows(_read_csv(name), name))
    return result


@router.get("/signals")
async def get_signals(symbol: str = Query("BTC/USDT")):
    """Compute the current signal state for each bot on the requested symbol."""
    from bot_macd import MACDBot
    from bot_rsi_vwap import RSIVWAPBot
    from bot_cvd import CVDBot

    exchange = config.make_exchange()
    results  = []

    bot_defs = [
        ("MACD",     MACDBot,    config.TIMEFRAMES["MACD"],     300),
        ("RSI_VWAP", RSIVWAPBot, config.TIMEFRAMES["RSI_VWAP"], 60),
        ("CVD",      CVDBot,     config.TIMEFRAMES["CVD"],       300),
    ]

    for name, BotClass, tf, limit in bot_defs:
        try:
            raw = exchange.fetch_ohlcv(symbol, tf, limit=limit)
            df  = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            df.set_index("timestamp", inplace=True)
            df = df.astype(float)

            if hasattr(BotClass, "precompute_indicators"):
                df = BotClass.precompute_indicators(df)

            stub   = object.__new__(BotClass)
            signal = stub.generate_signal(df, None)
            results.append({"bot": name, "symbol": symbol, "signal": signal, "timeframe": tf})
        except Exception as exc:
            results.append({"bot": name, "symbol": symbol, "signal": None, "error": str(exc)})

    return results
