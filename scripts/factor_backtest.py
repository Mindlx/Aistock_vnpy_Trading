#!/usr/bin/env python3
"""Factor-only backtest wrapper — runs ML subsystem backtest with correct DB path.

Usage:
    python scripts/factor_backtest.py

The factor_backtest.py in systems/MindLynx-Aistock/ defaults db_path
to "data/stock_analysis.db" which resolves to the empty fusion-level DB.
This wrapper passes the correct ML subsystem DB path.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ML_SUBSYS_DB = str(PROJECT_ROOT / "systems" / "MindLynx-Aistock" / "data" / "stock_analysis.db")

sys.path.insert(0, str(PROJECT_ROOT / "systems" / "MindLynx-Aistock"))

from src.core.factor_backtest import evaluate_factor_signals, print_factor_backtest_report

result = evaluate_factor_signals(db_path=ML_SUBSYS_DB)
print_factor_backtest_report(result)
