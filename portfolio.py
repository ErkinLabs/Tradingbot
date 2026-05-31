"""Portfolio-level aggregation across all bots."""

from __future__ import annotations

import config


def portfolio_summary(bots: list) -> dict:
    """Aggregate live stats from running bot instances."""
    stats = [b.get_stats() for b in bots]
    total_balance    = sum(s["balance"] for s in stats)
    total_equity     = sum(s["equity"] for s in stats)
    total_unrealized = sum(s["unrealized_pnl"] for s in stats)
    daily_pnl        = sum(s["daily_pnl"] for s in stats)
    daily_realized   = sum(s["daily_realized_pnl"] for s in stats)
    total_pnl        = sum(s["total_pnl"] for s in stats)
    trades_today     = sum(s["trades_today"] for s in stats)
    open_positions   = sum(len(b.positions) for b in bots)
    initial          = config.INITIAL_BALANCE

    return {
        "initial_balance":    initial,
        "total_balance":      round(total_balance, 2),
        "total_equity":       round(total_equity, 2),
        "total_unrealized":   round(total_unrealized, 4),
        "daily_pnl":          round(daily_pnl, 4),
        "daily_pnl_pct":      round(daily_pnl / initial * 100, 2) if initial else 0.0,
        "daily_realized_pnl": round(daily_realized, 4),
        "total_pnl":          round(total_pnl, 4),
        "total_return_pct":   round((total_equity - initial) / initial * 100, 2) if initial else 0.0,
        "trades_today":       trades_today,
        "open_positions":     open_positions,
        "bots":               stats,
    }
