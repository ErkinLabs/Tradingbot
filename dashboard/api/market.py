"""Market data endpoints — OHLCV and ticker via ccxt/Bybit."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import ccxt
from fastapi import APIRouter, HTTPException

import config

router = APIRouter()

_exchange = ccxt.bybit(config.EXCHANGE_OPTS)


def _norm(symbol: str) -> str:
    """Accept BTC-USDT or BTC/USDT and normalise to BTC/USDT."""
    return symbol.replace("-", "/").upper()


@router.get("/ohlcv/{symbol:path}")
async def get_ohlcv(symbol: str, timeframe: str = "5m", limit: int = 200):
    try:
        raw = _exchange.fetch_ohlcv(_norm(symbol), timeframe, limit=min(limit, 500))
        return [
            {
                "time": int(c[0] / 1000),
                "open": c[1],
                "high": c[2],
                "low": c[3],
                "close": c[4],
                "volume": c[5],
            }
            for c in raw
        ]
    except ccxt.BadSymbol as exc:
        raise HTTPException(status_code=400, detail=f"Unknown symbol: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/ticker/{symbol:path}")
async def get_ticker(symbol: str):
    try:
        t = _exchange.fetch_ticker(_norm(symbol))
        return {
            "last":       t.get("last") or 0,
            "change_pct": t.get("percentage") or 0,
            "high_24h":   t.get("high") or 0,
            "low_24h":    t.get("low") or 0,
            "volume_24h": t.get("baseVolume") or 0,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
