"""
Portfolio-level risk gates shared across all strategy bots.

Enforces:
  - Max concurrent open positions across the whole portfolio
  - Max open positions per bot
  - One open position per symbol (no duplicate coin exposure)
"""

from __future__ import annotations

from typing import Callable

import config


class PortfolioRiskManager:
    """Cross-bot risk checks before opening a new simulated position."""

    def __init__(self, bots_ref: Callable[[], list]) -> None:
        self._bots_ref = bots_ref

    def can_open(self, bot, symbol: str) -> tuple[bool, str]:
        bots = self._bots_ref()
        total_open = 0

        for b in bots:
            with b._positions_lock:
                total_open += len(b.positions)
                if symbol in b.positions and b is not bot:
                    return False, "symbol_open_other_bot"

        if total_open >= config.MAX_PORTFOLIO_POSITIONS:
            return False, "portfolio_max_positions"

        with bot._positions_lock:
            if symbol in bot.positions:
                return False, "already_open"
            if len(bot.positions) >= config.MAX_POSITIONS_PER_BOT:
                return False, "bot_max_positions"

        return True, ""

    def open_positions_count(self) -> int:
        return sum(len(b.positions) for b in self._bots_ref())

    def symbols_with_positions(self) -> set[str]:
        out: set[str] = set()
        for b in self._bots_ref():
            with b._positions_lock:
                out.update(b.positions.keys())
        return out
