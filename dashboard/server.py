"""
Web dashboard server — FastAPI + uvicorn.

Run standalone:
    python -m dashboard.server

Or launch from main.py via --with-dashboard flag.
"""

from __future__ import annotations

import os
import sys

# Ensure the project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ccxt's bundled toolz runs `git describe` at import time to detect its version.
# On Windows, git walks up from site-packages and may find an unrelated ~/.git
# (e.g. an empty repo in the home directory), producing a spurious
# "fatal: bad revision 'HEAD'" on stderr.  Capping the search at AppData stops
# git before it reaches the home directory.
if sys.platform == "win32" and not os.environ.get("GIT_CEILING_DIRECTORIES"):
    _appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA", "")
    if _appdata:
        os.environ["GIT_CEILING_DIRECTORIES"] = os.path.dirname(_appdata)

from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import config

from dashboard.auth import DashboardAuthMiddleware
from dashboard.api.market import router as market_router
from dashboard.api.trades import router as trades_router
from dashboard.api.ws import router as ws_router

_STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Trading Bot Dashboard", docs_url=None, redoc_url=None)

app.add_middleware(DashboardAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(market_router, prefix="/api")
app.include_router(trades_router, prefix="/api")
app.include_router(ws_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "paper_trading": config.PAPER_TRADING}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")


# ── Shared live bot references ────────────────────────────────────────────────
# Populated by main.py when --with-dashboard is active.

_live_bots: list = []


def register_bots(bots: list) -> None:
    global _live_bots
    _live_bots = bots


def get_live_bots() -> list:
    return _live_bots


# ── Runner ────────────────────────────────────────────────────────────────────

def run(host: str = "0.0.0.0", port: int = 7000) -> None:
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    run()
