"""
audit_macd_exits.py — Breakdown of MACD trade exit reasons.

Shows what % of trades close via signal vs SL vs TP vs end-of-data,
and compares PnL stats per exit type.
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import config
from bot_macd import MACDBot
from backtest import BacktestEngine, DataLoader, calculate_metrics
from rich.console import Console
from rich.table import Table
from rich import box
from collections import Counter

console = Console()

SYMBOL        = "BTC/USDT"
START         = "2024-01-01"
END           = "2024-12-31"
BALANCE       = 3_300.0
COMMISSION    = 0.00055

def run_audit():
    loader   = DataLoader()
    strategy = MACDBot()
    tf       = config.TIMEFRAMES["MACD"]

    console.print(f"\n[bold cyan]MACD Exit Audit[/bold cyan]  {SYMBOL}  {tf}  {START} to {END}")

    df = loader.fetch_range(SYMBOL, tf, START, END)
    console.print(f"  Data: {len(df):,} bars  ({df.index[0].date()} to {df.index[-1].date()})")

    engine = BacktestEngine(
        strategy=strategy,
        df=df,
        symbol=SYMBOL,
        initial_balance=BALANCE,
        commission_rate=COMMISSION,
    )
    result = engine.run()
    calculate_metrics(result)

    trades = result.trades
    if not trades:
        console.print("[red]No trades.[/red]")
        return

    # ── Exit reason breakdown ─────────────────────────────────────────────────
    reasons = Counter(t.reason for t in trades)
    total   = len(trades)

    tbl = Table(title="Exit Reason Breakdown", box=box.SIMPLE_HEAVY, header_style="bold cyan")
    tbl.add_column("Reason",       style="bold white", min_width=16)
    tbl.add_column("Count",        justify="right")
    tbl.add_column("% of Trades",  justify="right")
    tbl.add_column("Wins",         justify="right")
    tbl.add_column("Win Rate",     justify="right")
    tbl.add_column("Avg Net PnL",  justify="right")
    tbl.add_column("Total PnL",    justify="right")

    for reason in ["signal", "stop_loss", "take_profit", "end_of_data"]:
        bucket = [t for t in trades if t.reason == reason]
        if not bucket:
            continue
        count    = len(bucket)
        wins     = sum(1 for t in bucket if t.net_pnl > 0)
        wr       = wins / count * 100
        avg_pnl  = sum(t.net_pnl for t in bucket) / count
        tot_pnl  = sum(t.net_pnl for t in bucket)
        pct      = count / total * 100

        pnl_color = "green" if avg_pnl >= 0 else "red"
        tot_color = "green" if tot_pnl >= 0 else "red"
        tbl.add_row(
            reason,
            str(count),
            f"{pct:.1f}%",
            str(wins),
            f"{wr:.1f}%",
            f"[{pnl_color}]{avg_pnl:+.4f}[/{pnl_color}]",
            f"[{tot_color}]{tot_pnl:+.4f}[/{tot_color}]",
        )

    console.print()
    console.print(tbl)

    # ── Hold time by exit reason ──────────────────────────────────────────────
    hold_tbl = Table(title="Hold Time by Exit Reason", box=box.SIMPLE_HEAVY, header_style="bold cyan")
    hold_tbl.add_column("Reason",    style="bold white", min_width=16)
    hold_tbl.add_column("Avg Bars",  justify="right")
    hold_tbl.add_column("Min Bars",  justify="right")
    hold_tbl.add_column("Max Bars",  justify="right")
    hold_tbl.add_column("Avg Mins",  justify="right")

    for reason in ["signal", "stop_loss", "take_profit", "end_of_data"]:
        bucket = [t for t in trades if t.reason == reason]
        if not bucket:
            continue
        bars  = [t.hold_bars for t in bucket]
        mins  = [(t.exit_time - t.entry_time).total_seconds() / 60 for t in bucket]
        hold_tbl.add_row(
            reason,
            f"{sum(bars)/len(bars):.1f}",
            str(min(bars)),
            str(max(bars)),
            f"{sum(mins)/len(mins):.0f}m",
        )

    console.print()
    console.print(hold_tbl)

    # ── Summary ───────────────────────────────────────────────────────────────
    signal_pct  = reasons.get("signal",      0) / total * 100
    sl_pct      = reasons.get("stop_loss",   0) / total * 100
    tp_pct      = reasons.get("take_profit", 0) / total * 100
    eod_pct     = reasons.get("end_of_data", 0) / total * 100

    console.print(f"\n[bold]Summary:[/bold]  {total} total trades")
    console.print(f"  Signal exit : [yellow]{reasons.get('signal', 0)}[/yellow] ({signal_pct:.1f}%)")
    console.print(f"  Stop loss   : [red]{reasons.get('stop_loss', 0)}[/red]   ({sl_pct:.1f}%)")
    console.print(f"  Take profit : [green]{reasons.get('take_profit', 0)}[/green] ({tp_pct:.1f}%)")
    console.print(f"  End of data : {reasons.get('end_of_data', 0)} ({eod_pct:.1f}%)")

    m = result.metrics
    console.print(f"\n  Total Return: [{'green' if m['total_return_pct'] >= 0 else 'red'}]{m['total_return_pct']:+.2f}%[/]  "
                  f"Win Rate: {m['win_rate_pct']:.2f}%  "
                  f"Sharpe: {m['sharpe']:.3f}  "
                  f"Profit Factor: {m['profit_factor']:.3f}")


if __name__ == "__main__":
    run_audit()
