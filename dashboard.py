"""
Dashboard — live terminal UI using the `rich` library.

Auto-refreshes every DASHBOARD_REFRESH_SECS seconds.
Displays:
  - Per-bot stats table (trades, win %, P&L, return %, Sharpe, balance)
  - Live open positions per bot
  - Combined P&L
  - ASCII bar chart comparing bot returns
"""

import threading
import time
from typing import List

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

import config


# ── Colour helpers ─────────────────────────────────────────────────────────────

def _pnl_colour(value: float) -> str:
    return "green" if value >= 0 else "red"


def _fmt_pct(value: float) -> Text:
    colour = _pnl_colour(value)
    return Text(f"{value:+.2f}%", style=colour)


def _fmt_pnl(value: float) -> Text:
    colour = _pnl_colour(value)
    return Text(f"{value:+.4f}", style=colour)


# ── ASCII bar chart ───────────────────────────────────────────────────────────

def _bar_chart(stats_list: list) -> str:
    max_abs = max((abs(s["return_pct"]) for s in stats_list), default=1) or 1
    bar_width = 30
    lines = ["  Bot Return Comparison", "  " + "─" * (bar_width + 20)]
    for s in stats_list:
        pct  = s["return_pct"]
        fill = int(abs(pct) / max_abs * bar_width)
        bar  = ("█" * fill).ljust(bar_width)
        sign = "+" if pct >= 0 else "-"
        lines.append(f"  {s['bot']:<10} {sign}{bar} {abs(pct):.2f}%")
    return "\n".join(lines)


# ── Main layout builder ───────────────────────────────────────────────────────

def build_layout(bots) -> Panel:
    # ── Stats table ───────────────────────────────────────────────────────────
    stats_list = [b.get_stats() for b in bots]

    tbl = Table(
        title="[bold cyan]Bot Performance[/bold cyan]",
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold magenta",
    )
    tbl.add_column("Bot",       style="bold white",  min_width=10)
    tbl.add_column("Trades",    justify="right")
    tbl.add_column("Win %",     justify="right")
    tbl.add_column("P&L (USDT)",justify="right")
    tbl.add_column("Return",    justify="right")
    tbl.add_column("Sharpe",    justify="right")
    tbl.add_column("Balance",   justify="right")
    tbl.add_column("Status",    justify="center")

    total_pnl    = 0.0
    total_bal    = 0.0

    for s in stats_list:
        status = "[yellow]PAUSED[/yellow]" if s["paused"] else "[green]ACTIVE[/green]"
        tbl.add_row(
            s["bot"],
            str(s["trades"]),
            f"{s['win_rate']:.1f}%",
            _fmt_pnl(s["total_pnl"]),
            _fmt_pct(s["return_pct"]),
            f"{s['sharpe']:.2f}",
            f"{s['balance']:.2f}",
            status,
        )
        total_pnl += s["total_pnl"]
        total_bal += s["balance"]

    # Total row
    initial_total = config.INITIAL_BALANCE
    total_return  = (total_bal - initial_total) / initial_total * 100
    tbl.add_section()
    tbl.add_row(
        "[bold]TOTAL[/bold]",
        "",
        "",
        _fmt_pnl(total_pnl),
        _fmt_pct(total_return),
        "",
        f"[bold]{total_bal:.2f}[/bold]",
        "",
    )

    # ── Open positions table ──────────────────────────────────────────────────
    pos_tbl = Table(
        title="[bold cyan]Open Positions[/bold cyan]",
        box=box.SIMPLE,
        show_header=True,
        header_style="bold blue",
    )
    pos_tbl.add_column("Bot",    style="bold white", min_width=10)
    pos_tbl.add_column("Symbol", min_width=10)
    pos_tbl.add_column("Side",   justify="center")
    pos_tbl.add_column("Entry",  justify="right")
    pos_tbl.add_column("Size",   justify="right")

    any_pos = False
    for bot in bots:
        for sym, pos in bot.positions.items():
            side_str = (
                "[green]LONG[/green]"  if pos["side"] == "long"
                else "[red]SHORT[/red]"
            )
            pos_tbl.add_row(
                bot.name,
                sym,
                side_str,
                f"{pos['entry_price']:.4f}",
                f"{pos['size']:.6f}",
            )
            any_pos = True
    if not any_pos:
        pos_tbl.add_row("—", "—", "—", "—", "—")

    # ── Bar chart ─────────────────────────────────────────────────────────────
    chart_text = _bar_chart(stats_list)

    from rich.columns import Columns
    from rich.console import Group

    content = Group(tbl, pos_tbl, Text(chart_text))
    return Panel(content, title="[bold yellow]Crypto Paper Trading Dashboard[/bold yellow]", border_style="bright_blue")


# ── Dashboard runner ──────────────────────────────────────────────────────────

class Dashboard:
    def __init__(self, bots) -> None:
        self.bots    = bots
        self._stop   = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="Dashboard")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        console = Console()
        with Live(
            build_layout(self.bots),
            console=console,
            refresh_per_second=1,
            screen=False,
        ) as live:
            while not self._stop.is_set():
                time.sleep(config.DASHBOARD_REFRESH_SECS)
                live.update(build_layout(self.bots))
