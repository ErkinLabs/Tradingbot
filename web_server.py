import os
import hmac
import hashlib
import base64
from datetime import datetime
from fastapi import FastAPI, HTTPException, Response, Request, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse

app = FastAPI(title="Crypto Trading Bot Web Dashboard")

SECRET_KEY = os.getenv("SHARED_SSO_SECRET", "sso-shared-secret-key-12345")
ADMIN_SESSION_VAL = "trading-bot-admin-session-ok"

# Ziyaretçi Sayacı
visitors_count = 0

@app.middleware("http")
async def add_visitor_and_header(request: Request, call_next):
    global visitors_count
    # HEAD ping isteklerini hariç tutarak yalnızca ana sayfa ziyaretlerini sayıyoruz
    if request.method == "GET" and request.url.path == "/":
        visitors_count += 1
    
    response = await call_next(request)
    response.headers["X-Visitor-Count"] = str(visitors_count)
    return response

@app.get("/auth/sso")
def sso_login(token: str, response: Response):
    try:
        # SSO Jetonunu çöz
        decoded = base64.b64decode(token).decode("utf-8")
        timestamp_str, project, signature = decoded.split(":")
        
        # Zaman aşımı kontrolü (60 saniye)
        age = int(datetime.utcnow().timestamp() * 1000) - int(timestamp_str)
        if age > 60000:
            raise HTTPException(status_code=401, detail="SSO jetonunun süresi dolmuş.")
            
        # HMAC-SHA256 İmza doğrulaması
        secret = SECRET_KEY.encode("utf-8")
        msg = f"{timestamp_str}:{project}".encode("utf-8")
        expected_sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
        
        if signature != expected_sig:
            raise HTTPException(status_code=401, detail="Geçersiz SSO imzası.")
            
        # Session çerezini ayarla
        response.set_cookie(
            key="session", 
            value=ADMIN_SESSION_VAL, 
            httponly=True, 
            secure=True, 
            samesite="strict",
            path="/"
        )
        return RedirectResponse(url="/", status_code=303)
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=400, detail=f"SSO doğrulaması başarısız oldu: {str(e)}")

@app.get("/", response_class=HTMLResponse)
def index_page(session: str = Cookie(None)):
    if session != ADMIN_SESSION_VAL:
        return """
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

    # trades.csv ve trading.log dosyalarını oku
    trades_rows = []
    log_content = "Sistem günlüğü bulunamadı veya henüz yazılmadı."

    if os.path.exists("logs/trades.csv"):
        try:
            with open("logs/trades.csv", "r") as f:
                lines = f.readlines()
                if len(lines) > 1:
                    headers = lines[0].strip().split(",")
                    for line in lines[1:][-15:]: # Son 15 işlem
                        row = line.strip().split(",")
                        if len(row) == len(headers):
                            trades_rows.append(dict(zip(headers, row)))
        except Exception as e:
            trades_rows = []

    if os.path.exists("logs/trading.log"):
        try:
            with open("logs/trading.log", "r") as f:
                # Son 40 satır
                log_lines = f.readlines()[-40:]
                log_content = "".join(log_lines)
        except Exception:
            pass

    # HTML Çıktısı oluştur
    trades_html = ""
    if trades_rows:
        trades_html = """
        <div class="overflow-x-auto">
            <table class="w-full text-left text-xs border-collapse">
                <thead>
                    <tr class="border-b border-gray-800 text-gray-400">
                        <th class="py-3 px-4">Zaman</th>
                        <th class="py-3 px-4">Bot</th>
                        <th class="py-3 px-4">Sembol</th>
                        <th class="py-3 px-4">Yön</th>
                        <th class="py-3 px-4">Fiyat</th>
                        <th class="py-3 px-4">Miktar</th>
                        <th class="py-3 px-4">Kâr/Zarar</th>
                    </tr>
                </thead>
                <tbody class="divide-y divide-gray-900">
        """
        for t in trades_rows:
            pnl = t.get("pnl", "0.0")
            pnl_val = float(pnl) if pnl else 0.0
            pnl_color = "text-green-400" if pnl_val > 0 else ("text-red-400" if pnl_val < 0 else "text-gray-400")
            pnl_text = f"+{pnl_val:.4f}" if pnl_val > 0 else f"{pnl_val:.4f}"
            
            trades_html += f"""
                    <tr class="hover:bg-gray-900/50 transition-colors">
                        <td class="py-3 px-4 text-gray-500 font-mono">{t.get("timestamp", "")}</td>
                        <td class="py-3 px-4 font-semibold text-gray-300">{t.get("bot", "")}</td>
                        <td class="py-3 px-4 font-mono text-cyan-400">{t.get("symbol", "")}</td>
                        <td class="py-3 px-4">
                            <span class="px-2 py-0.5 rounded text-[10px] font-bold {'bg-green-500/10 text-green-400' if t.get('side','').upper() == 'BUY' else 'bg-red-500/10 text-red-400'}">
                                {t.get("side", "").upper()}
                            </span>
                        </td>
                        <td class="py-3 px-4 font-mono">{t.get("price", "")}</td>
                        <td class="py-3 px-4 font-mono">{t.get("amount", "")}</td>
                        <td class="py-3 px-4 font-mono font-semibold {pnl_color}">{pnl_text} USDT</td>
                    </tr>
            """
        trades_html += "</tbody></table></div>"
    else:
        trades_html = """
        <div class="flex flex-col items-center justify-center py-12 text-gray-500">
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-8 h-8 mb-2">
                <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
            </svg>
            <p class="text-sm">Henüz gerçekleşmiş bir işlem kaydı bulunmamaktadır.</p>
        </div>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html lang="tr">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Crypto Trading Bot Dashboard</title>
            <script src="https://cdn.tailwindcss.com"></script>
            <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Outfit:wght@300;400;600;700&display=swap" rel="stylesheet">
            <style>
                body {{
                    font-family: 'Outfit', sans-serif;
                }}
                .code-font {{
                    font-family: 'JetBrains Mono', monospace;
                }}
            </style>
        </head>
        <body class="bg-gray-950 text-white min-h-screen p-6 md:p-8 relative overflow-x-hidden">
            <div class="absolute top-0 right-0 w-[400px] h-[400px] rounded-full bg-cyan-500/5 blur-[120px] pointer-events-none z-0"></div>
            
            <main class="max-w-7xl mx-auto flex flex-col gap-6 relative z-10">
                <!-- Header -->
                <header class="flex flex-col sm:flex-row sm:items-center justify-between gap-4 p-5 rounded-2xl border border-gray-800 bg-gray-900/30 backdrop-blur-xl">
                    <div class="flex items-center gap-3">
                        <div class="w-10 h-10 rounded-xl border border-cyan-500/30 bg-cyan-500/10 flex items-center justify-center shadow-lg">
                            <span class="relative flex h-3 w-3">
                                <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-cyan-400 opacity-75"></span>
                                <span class="relative inline-flex rounded-full h-3 w-3 bg-cyan-500"></span>
                            </span>
                        </div>
                        <div>
                            <h1 class="font-bold text-lg tracking-tight">Crypto Trading Bot</h1>
                            <p class="text-xs text-gray-400 font-light mt-0.5">Paper Trading System & Live Diagnostics</p>
                        </div>
                    </div>
                    <div class="flex items-center gap-2 text-xs text-gray-400 bg-gray-900 border border-gray-800 px-4 py-2 rounded-xl">
                        <span class="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse"></span>
                        <span>Toplam Ziyaretçi: {visitors_count}</span>
                    </div>
                </header>

                <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
                    <!-- Left: Trades list -->
                    <div class="lg:col-span-2 bg-gray-900/30 border border-gray-800 rounded-2xl p-6 flex flex-col gap-4">
                        <div class="flex items-center justify-between border-b border-gray-800 pb-4">
                            <h2 class="font-semibold text-sm uppercase tracking-wider text-cyan-400">Son İşlem Geçmişi (trades.csv)</h2>
                        </div>
                        {trades_html}
                    </div>

                    <!-- Right: Log terminal -->
                    <div class="bg-gray-900/30 border border-gray-800 rounded-2xl p-6 flex flex-col gap-4">
                        <h2 class="font-semibold text-sm uppercase tracking-wider text-cyan-400">Sistem Günlüğü (trading.log)</h2>
                        <pre class="code-font text-[10px] leading-relaxed text-green-400 bg-black p-4 rounded-xl border border-gray-900 h-[480px] overflow-y-auto whitespace-pre-wrap select-all">{log_content}</pre>
                    </div>
                </div>
            </main>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content)
