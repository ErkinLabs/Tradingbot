"""
Dashboard — live terminal UI using the `rich` library.

Layout:
  ┌──────────────────────────────────┬────────────────────┐
  │  Charts (70%)                    │  Right panel (30%) │
  │  ─ Candlestick chart             │  ─ P&L Summary     │
  │  ─ Volume                        │  ─ Açık Pozisyonlar│
  │  ─ MACD (12/26/9)                │  ─ Son İşlemler    │
  │  ─ RSI (14)                      │                    │
  │  ─ CVD (proxy)                   │                    │
  ├──────────────────────────────────┴────────────────────┤
  │  Status bar: bot name · status · last signal · trades │
  └────────────────────────────────────────────────────────┘

Refreshes every second so open-position unrealized P&L stays live.
"""

import os
import threading
import time
from datetime import datetime, timezone
from typing import List, Tuple

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

import config

try:
    import pandas_ta as ta
    _TA_OK = True
except ImportError:
    _TA_OK = False


# ── Colour / format helpers ────────────────────────────────────────────────────

def _style(value: float) -> str:
    return "bright_green" if value >= 0 else "bright_red"


def _pnl_text(value: float, decimals: int = 4) -> Text:
    fmt = f"+.{decimals}f"
    return Text(f"{value:{fmt}}", style=_style(value))


def _pct_text(value: float) -> Text:
    return Text(f"{value:+.2f}%", style=_style(value))


# ── Data helpers ───────────────────────────────────────────────────────────────

def _get_chart_df(bots):
    """Return (DataFrame, symbol) for the chart — BTC/USDT from first live buffer."""
    symbol = config.SYMBOLS[0]
    for bot in bots:
        buf = bot._buffers.get(symbol)
        if buf is not None:
            df = buf.get_df()
            if not df.empty and len(df) > 10:
                return df, symbol
    return None, symbol


def _daily_pnl(bot) -> float:
    today = datetime.now(timezone.utc).date()
    with bot._trades_lock:
        return sum(
            t["net_pnl"]
            for t in bot.closed_trades
            if datetime.fromisoformat(t["timestamp"]).date() == today
        )


def _unrealized(bot, symbol: str, pos: dict) -> Tuple[float, float, float]:
    """Return (unrealized_usdt, unrealized_pct, current_price)."""
    buf = bot._buffers.get(symbol)
    price = (buf.last_price if buf and buf.last_price else None) or pos["entry_price"]
    pct  = (price - pos["entry_price"]) / pos["entry_price"] * 100
    usdt = (price - pos["entry_price"]) * pos["size"]
    return usdt, pct, price


def _duration(pos: dict) -> str:
    opened = pos["opened_at"]
    if isinstance(opened, str):
        opened = datetime.fromisoformat(opened)
    secs = int((datetime.now(timezone.utc) - opened).total_seconds())
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m{secs % 60:02d}s"
    return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"


def _last_signal_ago(bot) -> str:
    candidates: list[datetime] = []
    with bot._trades_lock:
        if bot.closed_trades:
            candidates.append(datetime.fromisoformat(bot.closed_trades[-1]["timestamp"]))
    with bot._positions_lock:
        for pos in bot.positions.values():
            opened = pos["opened_at"]
            if isinstance(opened, str):
                opened = datetime.fromisoformat(opened)
            candidates.append(opened)
    if not candidates:
        return "—"
    ago = int((datetime.now(timezone.utc) - max(candidates)).total_seconds())
    if ago < 60:
        return f"{ago}s ago"
    if ago < 3600:
        return f"{ago // 60}m ago"
    return f"{ago // 3600}h{(ago % 3600) // 60:02d}m ago"


# ── ASCII chart renderers ──────────────────────────────────────────────────────

def _candle_lines(df, n: int = 68, h: int = 15) -> List[Text]:
    if df is None or df.empty or len(df) < 2:
        return [Text("  No data yet…", style="dim")] * h

    n   = min(n, len(df))
    sub = df.tail(n)
    lo  = sub["low"].min()
    hi  = sub["high"].max()
    rng = hi - lo or lo * 0.001

    def row_of(price: float) -> int:
        return h - 1 - round((price - lo) / rng * (h - 1))

    # grid[row][col] = (char, style)
    grid: List[List[Tuple[str, str]]] = [
        [(" ", "") for _ in range(n * 2)] for _ in range(h)
    ]

    for i, (_, c) in enumerate(sub.iterrows()):
        x     = i * 2
        bull  = c["close"] >= c["open"]
        color = "bright_green" if bull else "bright_red"
        r_hi  = max(0, row_of(c["high"]))
        r_lo  = min(h - 1, row_of(c["low"]))
        r_op  = row_of(c["open"])
        r_cl  = row_of(c["close"])
        btop  = min(r_op, r_cl)
        bbot  = max(r_op, r_cl)

        for r in range(r_hi, r_lo + 1):
            ch = "█" if btop <= r <= bbot else "│"
            grid[r][x] = (ch, color)

    lines = []
    for ri, row in enumerate(grid):
        t = Text()
        for ch, st in row:
            t.append(ch, style=st) if st else t.append(ch)
        price_at = hi - (ri / max(h - 1, 1)) * rng
        t.append(f" {price_at:>10,.2f}", style="dim white")
        lines.append(t)
    return lines


def _volume_lines(df, n: int = 68, h: int = 4) -> List[Text]:
    if df is None or df.empty:
        return [Text("  No data", style="dim")] * h

    n   = min(n, len(df))
    sub = df.tail(n)
    max_vol = sub["volume"].max() or 1

    lines = []
    for r in range(h):
        t = Text()
        for _, c in sub.iterrows():
            bar_h = round(c["volume"] / max_vol * h)
            fill  = (h - 1 - r) < bar_h
            color = "bright_green" if c["close"] >= c["open"] else "bright_red"
            t.append("█" if fill else " ", style=color if fill else "")
            t.append(" ")
        lines.append(t)
    return lines


def _macd_lines(df, n: int = 68, h: int = 5) -> List[Text]:
    if df is None or df.empty or not _TA_OK:
        msg = "  MACD: pandas_ta unavailable" if not _TA_OK else "  MACD: no data"
        return [Text(msg, style="dim")] * h
    try:
        mdf = ta.macd(df["close"], fast=12, slow=26, signal=9)
        if mdf is None or mdf.empty:
            return [Text("  MACD: insufficient data", style="dim")] * h
        hist_col = next((c for c in mdf.columns if "MACDh" in c), None)
        if hist_col is None:
            return [Text("  MACD: no histogram column", style="dim")] * h
        hist = mdf[hist_col].dropna().tail(n)
        if hist.empty:
            return [Text("  MACD: empty", style="dim")] * h

        max_abs = max(abs(hist).max(), 1e-10)
        mid = h // 2

        lines = []
        for r in range(h):
            t = Text()
            for val in hist:
                color = "bright_green" if val >= 0 else "bright_red"
                bar   = round(abs(val) / max_abs * mid)
                in_up = val >= 0 and (mid - bar) <= r < mid
                in_dn = val <  0 and mid < r <= (mid + bar)
                if r == mid:
                    t.append("─", style="dim")
                elif in_up or in_dn:
                    t.append("█", style=color)
                else:
                    t.append(" ")
                t.append(" ")
            lines.append(t)
        return lines
    except Exception:
        return [Text("  MACD: error", style="dim")] * h


def _rsi_lines(df, n: int = 68, h: int = 4) -> List[Text]:
    if df is None or df.empty or not _TA_OK:
        msg = "  RSI: pandas_ta unavailable" if not _TA_OK else "  RSI: no data"
        return [Text(msg, style="dim")] * h
    try:
        rsi_s = ta.rsi(df["close"], length=14)
        if rsi_s is None:
            return [Text("  RSI: insufficient data", style="dim")] * h
        rsi = rsi_s.dropna().tail(n)
        if rsi.empty:
            return [Text("  RSI: empty", style="dim")] * h

        ob_row = round((1 - 70 / 100) * (h - 1))
        os_row = round((1 - 30 / 100) * (h - 1))

        lines = []
        for r in range(h):
            t = Text()
            for val in rsi:
                norm_r = round((1 - val / 100) * (h - 1))
                if norm_r == r:
                    color = "bright_red" if val >= 70 else ("bright_green" if val <= 30 else "yellow")
                    t.append("●", style=color)
                elif r == ob_row or r == os_row:
                    t.append("·", style="dim")
                else:
                    t.append(" ")
                t.append(" ")
            if r == ob_row:
                t.append("70", style="dim red")
            elif r == os_row:
                t.append("30", style="dim green")
            lines.append(t)
        return lines
    except Exception:
        return [Text("  RSI: error", style="dim")] * h


def _cvd_lines(df, n: int = 68, h: int = 4) -> List[Text]:
    if df is None or df.empty or len(df) < 2:
        return [Text("  No CVD data", style="dim")] * h

    sub  = df.tail(n).copy()
    sign = sub["close"].diff().fillna(0).apply(lambda x: 1 if x >= 0 else -1)
    cvd  = (sub["volume"] * sign).cumsum()

    lo  = cvd.min()
    hi  = cvd.max()
    rng = hi - lo or 1

    rows = [h - 1 - round((v - lo) / rng * (h - 1)) for v in cvd]

    lines = []
    for r in range(h):
        t = Text()
        for i, pr in enumerate(rows):
            if pr == r:
                t.append("●", style="cyan")
            elif i > 0 and min(rows[i - 1], pr) < r <= max(rows[i - 1], pr):
                t.append("│", style="cyan")
            else:
                t.append(" ")
            t.append(" ")
        lines.append(t)
    return lines


# ── Right-panel builders ───────────────────────────────────────────────────────

def _build_pnl_panel(bots) -> Panel:
    stats_list = [b.get_stats() for b in bots]

    tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold", padding=(0, 1))
    tbl.add_column("Bot",     style="bold white", min_width=10)
    tbl.add_column("Toplam",  justify="right", min_width=10)
    tbl.add_column("Günlük",  justify="right", min_width=10)
    tbl.add_column("Bakiye",  justify="right", min_width=9)

    total_pnl = 0.0
    total_bal = 0.0

    for bot, s in zip(bots, stats_list):
        dpnl   = _daily_pnl(bot)
        dot    = "[yellow]●[/yellow]" if s["paused"] else "[green]●[/green]"
        tbl.add_row(
            f"{dot} {s['bot']}",
            _pnl_text(s["total_pnl"]),
            _pnl_text(dpnl),
            f"{s['balance']:.2f}",
        )
        total_pnl += s["total_pnl"]
        total_bal += s["balance"]

    total_ret = (total_bal - config.INITIAL_BALANCE) / config.INITIAL_BALANCE * 100
    tbl.add_section()
    tbl.add_row(
        "[bold]TOTAL[/bold]",
        _pnl_text(total_pnl),
        Text(""),
        Text(f"[bold]{total_bal:.2f}[/bold]\n[dim]{total_ret:+.2f}%[/dim]"),
    )

    return Panel(tbl, title="[bold]Kâr / Zarar[/bold]", border_style="green")


def _build_positions_panel(bots) -> Panel:
    tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold", padding=(0, 1))
    tbl.add_column("Bot",    style="bold white", min_width=8)
    tbl.add_column("Pair",   min_width=12)
    tbl.add_column("Giriş",  justify="right", min_width=9)
    tbl.add_column("Anlık",  justify="right", min_width=9)
    tbl.add_column("Unrlzd", justify="right", min_width=7)
    tbl.add_column("Süre",   justify="right", min_width=7)

    any_pos = False
    for bot in bots:
        with bot._positions_lock:
            positions = dict(bot.positions)
        for sym, pos in positions.items():
            usdt, pct, price = _unrealized(bot, sym, pos)
            tbl.add_row(
                bot.name,
                sym,
                f"{pos['entry_price']:.4f}",
                f"{price:.4f}",
                _pct_text(pct),
                _duration(pos),
            )
            any_pos = True

    if not any_pos:
        tbl.add_row("[dim]—[/dim]", "[dim]Açık pozisyon yok[/dim]", "", "", "", "")

    return Panel(tbl, title="[bold]Açık Pozisyonlar[/bold]", border_style="blue")


def _build_trades_panel(bots) -> Panel:
    all_trades: list = []
    for bot in bots:
        with bot._trades_lock:
            all_trades.extend(bot.closed_trades)
    all_trades.sort(key=lambda t: t["timestamp"], reverse=True)
    recent = all_trades[:10]

    tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold", padding=(0, 1))
    tbl.add_column("Zaman",  min_width=7)
    tbl.add_column("Bot",    min_width=8)
    tbl.add_column("Pair",   min_width=12)
    tbl.add_column("Giriş",  justify="right", min_width=9)
    tbl.add_column("Çıkış",  justify="right", min_width=9)
    tbl.add_column("P&L",    justify="right", min_width=9)

    if not recent:
        tbl.add_row("[dim]—[/dim]", "[dim]Henüz işlem yok[/dim]", "", "", "", "")
    else:
        for t in recent:
            ts  = datetime.fromisoformat(t["timestamp"])
            ago = int((datetime.now(timezone.utc) - ts).total_seconds())
            if ago < 60:
                ts_str = f"{ago}s"
            elif ago < 3600:
                ts_str = f"{ago // 60}m"
            elif ts.date() == datetime.now(timezone.utc).date():
                ts_str = ts.strftime("%H:%M")
            else:
                ts_str = ts.strftime("%m/%d")
            tbl.add_row(
                ts_str,
                t["bot_name"],
                t["symbol"],
                f"{t['entry_price']:.4f}",
                f"{t['exit_price']:.4f}",
                _pnl_text(t["net_pnl"]),
            )

    return Panel(tbl, title="[bold]Son İşlemler (10)[/bold]", border_style="magenta")


# ── Status bar ─────────────────────────────────────────────────────────────────

def _build_status_bar(bots) -> Panel:
    t = Text()
    for i, bot in enumerate(bots):
        if i > 0:
            t.append("   │   ", style="dim")
        s      = bot.get_stats()
        paused = s["paused"]
        dot    = Text("● ", style="yellow" if paused else "bright_green")
        label  = "PAUSED" if paused else "ACTIVE"
        color  = "yellow" if paused else "bright_green"
        t.append_text(dot)
        t.append(bot.name, style="bold white")
        t.append(f" [{label}]", style=color)
        t.append(f"  ⏱ {_last_signal_ago(bot)}", style="dim")
        t.append(f"  {s['trades']} trades", style="dim cyan")
        t.append(f"  {s['win_rate']:.0f}% win", style="dim")

    return Panel(t, border_style="bright_black")


# ── Left-panel wrappers ────────────────────────────────────────────────────────

def _build_candle_panel(df, symbol: str) -> Panel:
    price = df["close"].iloc[-1] if df is not None and not df.empty else None
    title = f"[bold cyan]{symbol}[/bold cyan]"
    if price:
        title += f"  [bold white]{price:,.4f}[/bold white] USDT"
    return Panel(Group(*_candle_lines(df)), title=title, border_style="cyan")


def _build_volume_panel(df) -> Panel:
    return Panel(Group(*_volume_lines(df)), title="[bold]Volume[/bold]", border_style="blue")


def _build_macd_panel(df) -> Panel:
    return Panel(Group(*_macd_lines(df)), title="[bold]MACD (12/26/9)[/bold]", border_style="magenta")


def _build_rsi_panel(df) -> Panel:
    return Panel(Group(*_rsi_lines(df)), title="[bold]RSI (14)[/bold]", border_style="yellow")


def _build_cvd_panel(df) -> Panel:
    return Panel(Group(*_cvd_lines(df)), title="[bold]CVD (proxy)[/bold]", border_style="cyan")


# ── Main layout ────────────────────────────────────────────────────────────────

def build_layout(bots) -> Layout:
    df, symbol = _get_chart_df(bots)

    layout = Layout()
    layout.split_column(
        Layout(name="main"),
        Layout(name="footer", size=3),
    )
    layout["main"].split_row(
        Layout(name="charts", ratio=7),
        Layout(name="right",  ratio=3),
    )
    layout["charts"].split_column(
        Layout(name="candles",  ratio=5),
        Layout(name="volume",   ratio=2),
        Layout(name="macd_pnl", ratio=2),
        Layout(name="rsi_pnl",  ratio=2),
        Layout(name="cvd_pnl",  ratio=2),
    )
    layout["right"].split_column(
        Layout(name="pnl",       ratio=3),
        Layout(name="positions", ratio=4),
        Layout(name="trades",    ratio=3),
    )

    layout["candles"].update(_build_candle_panel(df, symbol))
    layout["volume"].update(_build_volume_panel(df))
    layout["macd_pnl"].update(_build_macd_panel(df))
    layout["rsi_pnl"].update(_build_rsi_panel(df))
    layout["cvd_pnl"].update(_build_cvd_panel(df))

    layout["pnl"].update(_build_pnl_panel(bots))
    layout["positions"].update(_build_positions_panel(bots))
    layout["trades"].update(_build_trades_panel(bots))
    layout["footer"].update(_build_status_bar(bots))

    return layout


# ── Dashboard runner ───────────────────────────────────────────────────────────

class Dashboard:
    """Live Rich dashboard — full-screen, 1-second refresh. Disabled when HEADLESS=true."""

    def __init__(self, bots) -> None:
        self.bots      = bots
        self._headless = os.getenv("HEADLESS", "").lower() in ("1", "true", "yes")
        self._stop     = threading.Event()
        self._thread   = threading.Thread(target=self._run, daemon=True, name="Dashboard")

    def start(self) -> None:
        if not self._headless:
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        # legacy_windows=False: use ANSI codes instead of Win32 API (supports Unicode in Windows Terminal)
        console = Console(legacy_windows=False)
        with Live(
            build_layout(self.bots),
            console=console,
            refresh_per_second=1,
            screen=True,
        ) as live:
            while not self._stop.is_set():
                time.sleep(1)
                live.update(build_layout(self.bots))
