# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run paper trading (all 3 bots, live market data)
python main.py

# Backtest a single strategy
python run_backtest.py --strategy macd --symbol BTC/USDT --start 2024-01-01 --end 2024-12-31
python run_backtest.py --strategy rsi_vwap --symbol SOL/USDT --start 2024-06-01 --end 2024-12-31
python run_backtest.py --strategy cvd --symbol BTC/USDT --start 2024-01-01 --end 2024-12-31

# Backtest all strategies with comparison report
python run_backtest.py --all --symbol BTC/USDT --start 2024-01-01 --end 2024-12-31

# Custom balance/commission
python run_backtest.py --strategy macd --symbol BTC/USDT --start 2024-01-01 --end 2024-12-31 --balance 5000 --commission 0.00055
```

No test suite exists yet. No linter is configured.

## Architecture

### Live Paper Trading

`main.py` starts three bots in separate threads. Each bot calls `run_once()` every `BOT_LOOP_SECS` (60s). A `Dashboard` instance auto-refreshes the Rich terminal UI every 10s. Graceful shutdown via SIGINT/SIGTERM prints final stats.

### Strategy Bots

All three bots inherit from `BaseBot` (`base_bot.py`), which provides:
- `ccxt.bybit` exchange connection (public API only — no credentials required)
- `fetch_ohlcv()` / `fetch_ticker()` / `current_price()`
- Simulated position management: one position per symbol max, sized at `MAX_POSITION_PCT` of bot balance
- SL/TP checking, daily-loss guard (pauses bot if drawdown exceeds `MAX_DAILY_LOSS_PCT`)
- Trade logging to `logs/trades.csv` and `logs/trading.log`
- `get_stats()` → trades, win rate, PnL, Sharpe, balance

Each bot must implement `generate_signal(df, position) → "buy" | "sell" | "close" | None`. This is the **shared interface** used by both live trading (`run_once` → `_process_symbol`) and backtesting.

| Bot | File | Timeframe | Signal logic |
|-----|------|-----------|--------------|
| MACD | `bot_macd.py` | 5m | MACD histogram zero-cross (long only) + EMA200 filter + volume spike (1.5×SMA) |
| RSI+VWAP | `bot_rsi_vwap.py` | 1h | RSI oversold (long only) + price vs VWAP + ADX<35 filter |
| CVD | `bot_cvd.py` | 15m | Bullish CVD divergence (long only) + EMA50 + 2-bar confirmation |

### Backtest System (`backtest/`)

- **`DataLoader`** (`data_loader.py`): fetches paginated OHLCV from Bybit via ccxt, caches to Parquet in `backtest/cache/`
- **`BacktestEngine`** (`engine.py`): bar-by-bar simulation. Key rules: no lookahead (strategy sees only `df.iloc[:i+1]`), fills at next bar's open (1-bar delay), SL/TP checked intra-bar using high/low
- **`calculate_metrics`** (`metrics.py`): Sharpe, Sortino, Calmar, max drawdown, profit factor, avg hold time, etc.
- **`generate_report` / `generate_comparison_report`** (`report.py`): HTML reports written to `backtest/reports/`

### Configuration (`config.py`)

Single source of truth for all parameters: capital allocation, symbols, risk params (SL 1.5%, TP 3%, max 10% position size), timeframes, exchange settings, and loop intervals. `PAPER_TRADING = True` is enforced as an assertion in `BaseBot.__init__` and `main()`. Exchange type is `spot` (no leverage, long-only).

### Adding a New Strategy

1. Create `bot_<name>.py`, subclass `BaseBot`, set `name = "<NAME>"`, implement `generate_signal(df, position)` and `run_once()`
2. Add the bot's timeframe and allocation to `config.py`
3. Register it in `run_backtest.py`'s `_make_strategy()` mapping and `main.py`'s bot list

## Stack
- Python 3.11
- ccxt (şu an aktif — Bybit OHLCV verisi)
- pybit (planlanan — WebSocket tick data geçişi)
- pandas_ta (teknik indikatörler)
- rich (terminal dashboard)
- pandas / numpy (veri işleme)

## Rules (Critical)
- PAPER_TRADING = True — asla False yapma
- API key ve secret asla koda yazma, .env kullan
- config.py tek kaynak — parametreleri direkt dosyalara yazma
- Her strateji kendi bot dosyasında kalmalı
- generate_signal() imzasını değiştirme — backtest sistemi buna bağımlı

## Planned Migrations
- ccxt → pybit WebSocket (tick-level data için)
- CVD hesaplaması gerçek order flow verisiyle yeniden yazılacak
- VWAP günlük reset eklenecek

## Project Structure
trade-bot/
├── main.py              # 3 botu thread'lerde başlatır
├── base_bot.py          # Tüm botların base class'ı
├── bot_macd.py
├── bot_rsi_vwap.py
├── bot_cvd.py
├── config.py            # Tek kaynak — tüm parametreler burada
├── run_backtest.py      # Backtest entry point
├── backtest/
│   ├── data_loader.py
│   ├── engine.py
│   ├── metrics.py
│   └── report.py
└── logs/
    ├── trades.csv
    └── trading.log