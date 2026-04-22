"""WebSocket endpoint — forwards live Bybit spot ticker to browser clients."""

from __future__ import annotations

import asyncio
import json
import logging

import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()
log    = logging.getLogger(__name__)

_BYBIT_WS = "wss://stream.bybit.com/v5/public/spot"


def _bybit_sym(symbol: str) -> str:
    """'BTC/USDT' or 'BTC-USDT' → 'BTCUSDT'"""
    return symbol.replace("/", "").replace("-", "").upper()


@router.websocket("/ws/price/{symbol:path}")
async def price_ws(websocket: WebSocket, symbol: str) -> None:
    await websocket.accept()
    bybit_sym = _bybit_sym(symbol)

    while True:
        try:
            async with websockets.connect(_BYBIT_WS, ping_interval=20) as bws:
                await bws.send(json.dumps({
                    "op":   "subscribe",
                    "args": [f"tickers.{bybit_sym}"],
                }))

                while True:
                    try:
                        raw  = await asyncio.wait_for(bws.recv(), timeout=35)
                        data = json.loads(raw)

                        if data.get("topic", "").startswith("tickers."):
                            d = data.get("data", {})
                            await websocket.send_json({
                                "last":       float(d.get("lastPrice",    0) or 0),
                                "change_pct": float(d.get("price24hPcnt", 0) or 0) * 100,
                                "high_24h":   float(d.get("highPrice24h", 0) or 0),
                                "low_24h":    float(d.get("lowPrice24h",  0) or 0),
                                "volume_24h": float(d.get("volume24h",    0) or 0),
                            })

                    except asyncio.TimeoutError:
                        await bws.send(json.dumps({"op": "ping"}))

        except WebSocketDisconnect:
            return
        except Exception as exc:
            log.warning("Bybit WS error — reconnecting: %s", exc)
            await asyncio.sleep(2)
