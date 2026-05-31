# Trading Bots — Paper Trading System

Bybit **spot** piyasasında üç strateji (MACD, RSI+VWAP, CVD) ile paper trading yapan Python bot sistemi. Canlı veri WebSocket kline akışı ile gelir; backtest motoru aynı `generate_signal()` arayüzünü kullanır.

## Gereksinimler

- Python 3.11+
- İnternet (Bybit public API / WebSocket)

## Kurulum

```bash
cd trading-bots
pip install -r requirements.txt
cp .env.example .env   # isteğe bağlı — parametre override
```

## Çalıştırma

```bash
# Paper trading (terminal dashboard)
python main.py

# Web dashboard ile (http://localhost:7000)
python main.py --with-dashboard

# Backtest
python run_backtest.py --strategy macd --symbol BTC/USDT --start 2024-01-01 --end 2024-12-31
python run_backtest.py --all --symbol BTC/USDT --start 2024-01-01 --end 2024-12-31
```

## Docker

```bash
docker build -t trading-bots .
docker run -p 7000:7000 -v ./logs:/app/logs -e DASHBOARD_API_KEY=your-secret trading-bots
```

`HEADLESS=true` ortam değişkeni terminal UI'ı kapatır; web dashboard port 7000'de açılır.

## Ortam değişkenleri

| Değişken | Açıklama | Varsayılan |
|----------|----------|------------|
| `INITIAL_BALANCE` | Toplam USDT | 1000 |
| `STOP_LOSS_PCT` | Stop-loss | 0.015 |
| `TAKE_PROFIT_PCT` | Take-profit | 0.030 |
| `SL_TP_CHECK_SECS` | Arka plan SL/TP poll aralığı | 5 |
| `DASHBOARD_API_KEY` | Web `/api/*` auth (boş = kapalı) | — |
| `TELEGRAM_BOT_TOKEN` | Telegram bildirim | — |
| `TELEGRAM_CHAT_ID` | Telegram chat ID | — |
| `USE_DYNAMIC_UNIVERSE` | 4s/günlük en hareketli coin taraması | true |
| `UNIVERSE_ACTIVE_COUNT` | Aktif coin sayısı | 4 |
| `UNIVERSE_RESCAN_HOURS` | Universe yenileme aralığı | 4 |
| `MAX_PORTFOLIO_POSITIONS` | Tüm botlarda max açık pozisyon | 3 |
| `MAX_POSITIONS_PER_BOT` | Bot başına max pozisyon | 1 |
| `MACD_VOL_MULT` | MACD hacim filtresi çarpanı | 1.5 |
| `MACD_ADX_MIN` | MACD minimum ADX | 18 |

## Dinamik universe & risk

- **Universe scanner** (`universe_scanner.py`): Günlük hacim whitelist + 4 saatte bir ATR/değişim skoru ile top N coin seçer. Açık pozisyonlu coinler listeden düşmez.
- **Portföy riski** (`portfolio_risk.py`): En fazla 3 eşzamanlı pozisyon (tüm botlar), aynı coinde tek pozisyon, bot başına 1 pozisyon.
- **MACD gevşetme**: Filtreler `config.py` / `.env` ile ayarlanır (varsayılan: vol×1.5, ADX≥18, RSI 40–78).

Coolify/deploy için `/app/logs` persistent volume önerilir; aksi halde redeploy bakiyeyi sıfırlar.

## Mimari

```
main.py
  ├── 3 bot (MACD, RSI_VWAP, CVD) → BaseBot
  ├── UniverseManager (opsiyonel dinamik coin seçimi)
  ├── PortfolioRiskManager (cross-bot pozisyon limiti)
  ├── KlineStreamManager (Bybit WS) → on_candle_close
  ├── risk_guard_loop (SL/TP her N saniye)
  └── terminal_dashboard / opsiyonel web dashboard

backtest/
  ├── data_loader.py   (spot OHLCV, parquet cache)
  ├── engine.py        (bar-by-bar simülasyon)
  ├── metrics.py
  └── report.py
```

**Canlı tetikleyici:** Mum kapanışı (`on_candle_close`). SL/TP ayrıca arka plan thread'inde ve mum high/low ile intra-bar kontrol edilir. Günlük zarar limiti yalnızca **yeni girişleri** durdurur; açık pozisyonlarda SL/TP devam eder.

**Güvenlik:** `PAPER_TRADING = True` zorunlu; gerçek emir gönderilmez.

## Testler

```bash
python -m pytest tests/ -v
```

CI: `.github/workflows/ci.yml`

## Strateji ekleme

1. `bot_<name>.py` — `BaseBot` alt sınıfı, `generate_signal()` + `_process_symbol()`
2. `config.py` — allocation, timeframe
3. `run_backtest.py` — strategy registry
4. `main.py` — bot listesine ekle

Detaylı geliştirici notları: `CLAUDE.md`
