"""
Backtesting module for the crypto paper trading system.
"""

from .engine import BacktestEngine, BacktestResult, Trade
from .data_loader import DataLoader
from .metrics import calculate_metrics
from .report import generate_report, generate_comparison_report

__all__ = [
    "BacktestEngine",
    "BacktestResult",
    "Trade",
    "DataLoader",
    "calculate_metrics",
    "generate_report",
    "generate_comparison_report",
]
