# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Commands

```bash
pip install -r requirements.txt

# Paper trading (WebSocket-driven, not polling)
python main.py
python main.py --with-dashboard

# Backtest
python run_backtest.py --strategy macd --symbol BTC/USDT --start 2024-01-01 --end 2024-12-31
python run_backtest.py --all --symbol BTC/USDT --start 2024-01-01 --end 2024-12-31

# Tests
python -m pytest tests/ -v
```

## Architecture

### Live Paper Trading

`main.py` starts three bots. Each bot receives `on_candle_close(symbol)` from `KlineStreamManager` when a kline confirms. A background `_risk_guard_loop` polls SL/TP every `SL_TP_CHECK_SECS` (default 5s). Terminal dashboard refreshes every second; optional web dashboard on port 7000.

### Strategy Bots

All bots inherit `BaseBot` (`base_bot.py`):

- Exchange via `config.make_exchange()` (spot Bybit by default)
- WebSocket kline buffers for OHLCV + intra-bar high/low SL/TP
- Simulated positions, commission, daily-loss guard (pauses entries only)
- State persistence in `logs/state_<BOT>.json`
- Trade log: `logs/trades.csv`

Shared interface: `generate_signal(df, position) → "buy" | "close" | None`

| Bot | File | Timeframe |
|-----|------|-----------|
| MACD | `bot_macd.py` | 5m |
| RSI+VWAP | `bot_rsi_vwap.py` | 1h (VWAP resets UTC midnight) |
| CVD | `bot_cvd.py` | 15m (bar-direction proxy; see `cvd_utils.py`) |

### Backtest (`backtest/`)

- `DataLoader`: spot OHLCV via `config.make_exchange()`, parquet cache in `backtest/cache/`
- `BacktestEngine`: no lookahead, next-bar open fills, intra-bar SL/TP on high/low
- `metrics.py`, `report.py`: HTML reports in `backtest/reports/`

### Configuration (`config.py`)

Single source of truth. `PAPER_TRADING = True` is enforced. Never commit `.env`.

### Web Dashboard Auth

Set `DASHBOARD_API_KEY` in `.env` to require `X-API-Key` header on `/api/*`. `/health` is always public.

## Stack

- Python 3.11+
- ccxt (REST warmup + backtest data)
- websocket-client (Bybit V5 kline stream)
- pandas_ta, pandas, numpy
- rich (terminal UI), FastAPI (web UI)
- pytest + httpx (tests)

## Rules (Critical)

- `PAPER_TRADING = True` — never disable without explicit user request
- No API secrets in code — use `.env`
- All parameters in `config.py`
- One strategy per `bot_*.py` file
- Do not change `generate_signal()` signature — backtest depends on it

## Project Structure

```
trading-bots/
├── main.py
├── base_bot.py
├── bot_macd.py / bot_rsi_vwap.py / bot_cvd.py
├── cvd_utils.py
├── config.py
├── kline_stream.py / kline_buffer.py
├── terminal_dashboard.py
├── run_backtest.py
├── dashboard/          # FastAPI web UI
├── backtest/
├── tests/
└── logs/
```

## Adding a Strategy

1. Create `bot_<name>.py` with `generate_signal()` and `_process_symbol()`
2. Add allocation + timeframe to `config.py`
3. Register in `run_backtest.py` and `main.py`
