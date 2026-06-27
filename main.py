"""
main.py — Entry point for the paper trading bot system.

Starts three bots (MACD, RSI+VWAP, CVD) in separate threads,
each executing their strategy every BOT_LOOP_SECS seconds.

Features
  - Per-bot daily-loss guard (pauses bot if limit hit)
  - Rich terminal dashboard (auto-refreshes every 10 s)
  - Graceful shutdown on Ctrl+C with final stats summary
"""

import signal
import sys
import threading
import time

from rich.console import Console
from rich.table import Table
from rich import box

import config
from bot_macd     import MACDBot
from bot_rsi_vwap import RSIVWAPBot
from bot_cvd      import CVDBot
from dashboard    import Dashboard

console = Console()


# ── Bot thread worker ─────────────────────────────────────────────────────────

def _bot_worker(bot, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            bot.run_once()
        except Exception as exc:
            bot.log.error("Unhandled exception in run_once: %s", exc, exc_info=True)
        stop_event.wait(timeout=config.BOT_LOOP_SECS)


# ── Startup summary ───────────────────────────────────────────────────────────

def _print_startup_summary(bots) -> None:
    tbl = Table(
        title="[bold cyan]Paper Trading System — Startup[/bold cyan]",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
    )
    tbl.add_column("Bot",        style="bold white",  min_width=12)
    tbl.add_column("Allocation", justify="right")
    tbl.add_column("Balance",    justify="right")
    tbl.add_column("Timeframe",  justify="center")
    tbl.add_column("Symbols",    justify="left")

    for bot in bots:
        alloc_pct = config.BOT_ALLOCATIONS.get(bot.name, 0) * 100
        tf        = config.TIMEFRAMES.get(bot.name, "?")
        tbl.add_row(
            bot.name,
            f"{alloc_pct:.0f}%",
            f"{bot.balance:.2f} USDT",
            tf,
            ", ".join(config.SYMBOLS),
        )

    console.print()
    console.print(tbl)
    console.print(
        f"  [yellow]Total capital:[/yellow] {config.INITIAL_BALANCE:,.2f} USDT  |  "
        f"[yellow]Paper trading:[/yellow] {'YES' if config.PAPER_TRADING else 'NO — DANGER'}\n"
    )


# ── Final stats ───────────────────────────────────────────────────────────────

def _print_final_stats(bots) -> None:
    console.print("\n[bold yellow]──── Final Performance ────[/bold yellow]")
    tbl = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold cyan")
    tbl.add_column("Bot",        style="bold white", min_width=10)
    tbl.add_column("Trades",     justify="right")
    tbl.add_column("Win %",      justify="right")
    tbl.add_column("P&L (USDT)", justify="right")
    tbl.add_column("Return",     justify="right")
    tbl.add_column("Sharpe",     justify="right")
    tbl.add_column("Balance",    justify="right")

    total_pnl = 0.0
    total_bal = 0.0
    for bot in bots:
        s = bot.get_stats()
        pnl_col = f"[{'green' if s['total_pnl'] >= 0 else 'red'}]{s['total_pnl']:+.4f}[/]"
        ret_col = f"[{'green' if s['return_pct'] >= 0 else 'red'}]{s['return_pct']:+.2f}%[/]"
        tbl.add_row(
            s["bot"],
            str(s["trades"]),
            f"{s['win_rate']:.1f}%",
            pnl_col,
            ret_col,
            f"{s['sharpe']:.2f}",
            f"{s['balance']:.2f}",
        )
        total_pnl += s["total_pnl"]
        total_bal += s["balance"]

    total_return = (total_bal - config.INITIAL_BALANCE) / config.INITIAL_BALANCE * 100
    tbl.add_section()
    pnl_col = f"[{'green' if total_pnl >= 0 else 'red'}]{total_pnl:+.4f}[/]"
    ret_col = f"[{'green' if total_return >= 0 else 'red'}]{total_return:+.2f}%[/]"
    tbl.add_row("[bold]TOTAL[/bold]", "", "", pnl_col, ret_col, "", f"[bold]{total_bal:.2f}[/bold]")

    console.print(tbl)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    assert config.PAPER_TRADING, "Set PAPER_TRADING=True before running."

    console.print("[bold green]Initialising bots…[/bold green]")
    bots = [MACDBot(), RSIVWAPBot(), CVDBot()]

    _print_startup_summary(bots)

    stop_event = threading.Event()

    # Handle Ctrl+C / SIGTERM
    def _shutdown(signum=None, frame=None):
        console.print("\n[bold red]Shutdown signal received — stopping bots…[/bold red]")
        stop_event.set()

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Start bot threads
    threads = []
    for bot in bots:
        t = threading.Thread(
            target=_bot_worker,
            args=(bot, stop_event),
            daemon=True,
            name=f"Bot-{bot.name}",
        )
        t.start()
        threads.append(t)
        console.print(f"  [green]✓[/green] {bot.name} bot started")

    # Start dashboard
    dash = Dashboard(bots)
    dash.start()
    console.print("\n[bold cyan]All bots running. Press Ctrl+C to stop.[/bold cyan]\n")

    # Wait until shutdown
    try:
        while not stop_event.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        _shutdown()

    # Graceful teardown
    stop_event.set()
    dash.stop()
    for t in threads:
        t.join(timeout=5)

    _print_final_stats(bots)
    console.print("\n[bold green]Shutdown complete.[/bold green]")


if __name__ == "__main__":
    main()
