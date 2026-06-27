"""Universe / tradable symbols endpoint for the web dashboard."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter

import config

router = APIRouter()


def _position_map(bots: list) -> tuple[set[str], dict[str, list[str]]]:
    pinned: set[str] = set()
    by_symbol: dict[str, list[str]] = {}
    for bot in bots:
        with bot._positions_lock:
            for sym in bot.positions:
                pinned.add(sym)
                by_symbol.setdefault(sym, []).append(bot.name)
    return pinned, by_symbol


@router.get("/universe")
async def get_universe():
    """Active tradable symbols, scores, and open-position pins."""
    from dashboard.server import get_live_bots, get_universe_manager

    bots = get_live_bots()
    mgr  = get_universe_manager()

    pinned, position_bots = _position_map(bots) if bots else (set(), {})

    if mgr is not None:
        active    = mgr.active_symbols
        merged    = mgr.get_symbols()
        scores    = mgr.last_scores
        whitelist = mgr.daily_whitelist
        dynamic   = config.USE_DYNAMIC_UNIVERSE
        # Sidebar: top N skor + pin’li coinler (aktif evren dışında kalan pozisyonlar)
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        top_n  = config.UNIVERSE_ACTIVE_COUNT
        display_set = {sym for sym, _ in ranked[:top_n]}
        display_set |= pinned
        display_syms = sorted(
            display_set,
            key=lambda s: (-scores.get(s, -1.0), s),
        )
        if not display_syms:
            display_syms = list(active)
    elif bots:
        merged    = bots[0].trading_symbols
        active    = list(merged)
        scores    = {}
        whitelist = list(config.FALLBACK_SYMBOLS)
        dynamic   = config.USE_DYNAMIC_UNIVERSE
        display_syms = list(merged)
    else:
        merged    = list(config.FALLBACK_SYMBOLS)
        active    = list(merged)
        scores    = {}
        whitelist = list(config.FALLBACK_SYMBOLS)
        dynamic   = False
        display_syms = list(merged)

    active_set = set(active)

    rows = []
    for sym in display_syms:
        base = sym.split("/")[0]
        in_active = sym in active_set
        is_pinned = sym in pinned
        if is_pinned and in_active:
            status = "active+pinned"
        elif is_pinned:
            status = "pinned"
        elif in_active:
            status = "active"
        elif sym in whitelist:
            status = "whitelist"
        else:
            status = "other"

        rows.append({
            "symbol":             sym,
            "base":               base,
            "score":              scores.get(sym),
            "status":             status,
            "in_universe":        in_active,
            "pinned":             is_pinned,
            "bots_with_position": position_bots.get(sym, []),
        })

    def sort_key(row: dict):
        score = row["score"]
        score_val = score if score is not None else -1.0
        return (
            0 if row["pinned"] else 1,
            0 if row["in_universe"] else 1,
            -score_val,
            row["symbol"],
        )

    rows.sort(key=sort_key)

    return {
        "dynamic_enabled": config.USE_DYNAMIC_UNIVERSE and mgr is not None,
        "active_count":    len(active_set),
        "pinned_count":    len(pinned),
        "whitelist_count": len(whitelist),
        "active":          sorted(active_set),
        "pinned":          sorted(pinned),
        "whitelist":       whitelist,
        "scan_status":     mgr.scan_status if mgr is not None else "static",
        "scan_message":    mgr.scan_message if mgr is not None else "",
        "symbols":         rows,
    }
