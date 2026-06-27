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
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import config

from dashboard.auth import DashboardAuthMiddleware
from dashboard.api.market import router as market_router
from dashboard.api.trades import router as trades_router
from dashboard.api.universe import router as universe_router
from dashboard.api.ws import router as ws_router

_STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Trading Bot Dashboard", docs_url=None, redoc_url=None)

SECRET_KEY = os.getenv("SHARED_SSO_SECRET", "sso-shared-secret-key-12345")
ADMIN_SESSION_VAL = "trading-bot-admin-session-ok"

# Ziyaretçi Sayacı
visitors_count = 0

@app.middleware("http")
async def sso_and_visitor_middleware(request: Request, call_next):
    global visitors_count
    path = request.url.path

    # GET / (sayfa yüklemesi) durumunda ziyaretçiyi artır
    if request.method == "GET" and path == "/":
        visitors_count += 1

    # Sağlık kontrolü ve SSO rotalarını hariç tut
    if path == "/auth/sso" or path == "/health" or path.startswith("/api/health"):
        response = await call_next(request)
        response.headers["X-Visitor-Count"] = str(visitors_count)
        return response

    # Çerez kontrolü (Dashboard sayfaları ve API rotaları için)
    session_cookie = request.cookies.get("session")
    if session_cookie != ADMIN_SESSION_VAL:
        # Erişim Engellendi HTML sayfasını dön
        denied_html = """
        <html>
            <head>
                <title>Crypto Trading Bot - Yetkisiz Erişim</title>
                <script src="https://cdn.tailwindcss.com"></script>
            </head>
            <body class="bg-gray-950 text-white min-h-screen flex items-center justify-center font-sans p-6">
                <div class="max-w-md w-full text-center bg-gray-900 border border-gray-800 rounded-2xl p-8 shadow-xl">
                    <div class="w-16 h-16 bg-red-500/10 border border-red-500/30 text-red-500 rounded-2xl flex items-center justify-center mx-auto mb-6">
                        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-8 h-8">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25 2.25v6.75a2.25 2.25 0 002.25 2.25z" />
                        </svg>
                    </div>
                    <h1 class="text-xl font-bold mb-2">Erişim Engellendi</h1>
                    <p class="text-gray-400 text-sm mb-6">Bu panele erişmek için yetkiniz bulunmamaktadır. Lütfen ErkinLabs Konsolu üzerinden güvenli bağlantı kurun.</p>
                    <a href="https://consol.erkinlabs.com" class="inline-block w-full py-3 bg-cyan-600 hover:bg-cyan-500 text-white text-sm font-semibold rounded-xl transition-all">ErkinLabs Konsolu'na Git</a>
                </div>
            </body>
        </html>
        """
        response = HTMLResponse(content=denied_html, status_code=403)
        response.headers["X-Visitor-Count"] = str(visitors_count)
        return response

    response = await call_next(request)
    response.headers["X-Visitor-Count"] = str(visitors_count)
    return response

@app.get("/auth/sso")
def sso_login(token: str):
    import hmac
    import hashlib
    import base64
    from datetime import datetime
    try:
        decoded = base64.b64decode(token).decode("utf-8")
        timestamp_str, project, signature = decoded.split(":")
        
        age = int(datetime.utcnow().timestamp() * 1000) - int(timestamp_str)
        if age > 60000:
            raise HTTPException(status_code=401, detail="SSO jetonunun süresi dolmuş.")
            
        secret = SECRET_KEY.encode("utf-8")
        msg = f"{timestamp_str}:{project}".encode("utf-8")
        expected_sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
        
        if signature != expected_sig:
            raise HTTPException(status_code=401, detail="Geçersiz SSO imzası.")
            
        redirect_resp = RedirectResponse(url="/", status_code=303)
        redirect_resp.set_cookie(
            key="session", 
            value=ADMIN_SESSION_VAL, 
            httponly=True, 
            secure=True, 
            samesite="lax",
            path="/"
        )
        return redirect_resp
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=400, detail=f"SSO doğrulaması başarısız oldu: {str(e)}")

app.add_middleware(DashboardAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(market_router, prefix="/api")
app.include_router(trades_router, prefix="/api")
app.include_router(universe_router, prefix="/api")
app.include_router(ws_router)


@app.get("/health")
async def health() -> dict:
    from dashboard.server import get_universe_manager

    mgr = get_universe_manager()
    return {
        "status": "ok",
        "paper_trading": config.PAPER_TRADING,
        "use_dynamic_universe": config.USE_DYNAMIC_UNIVERSE,
        "universe_active_count": config.UNIVERSE_ACTIVE_COUNT,
        "universe_scan_status": mgr.scan_status if mgr else None,
        "universe_active_symbols": len(mgr.active_symbols) if mgr else None,
    }


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")


# ── Shared live bot references ────────────────────────────────────────────────
# Populated by main.py when --with-dashboard is active.

_live_bots: list = []
_universe_manager = None


def register_bots(bots: list) -> None:
    global _live_bots
    _live_bots = bots


def register_universe(manager) -> None:
    global _universe_manager
    _universe_manager = manager


def get_live_bots() -> list:
    return _live_bots


def get_universe_manager():
    return _universe_manager


# ── Runner ────────────────────────────────────────────────────────────────────

def run(host: str = "0.0.0.0", port: int = 7000) -> None:
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    run()
