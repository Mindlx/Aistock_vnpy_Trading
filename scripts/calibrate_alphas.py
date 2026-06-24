"""
Alpha calibration script — reads backtest results and updates STOCK_ALPHA_OVERRIDE.

Run monthly (systemd timer) or manually after backtest --force.
Updates src/reliability.py with per-stock alpha values based on recent sentiment accuracy.

Usage:
    .venv/bin/python scripts/calibrate_alphas.py              # normal mode
    .venv/bin/python scripts/calibrate_alphas.py --dry-run     # preview only
    .venv/bin/python scripts/calibrate_alphas.py --min-samples 30  # override min sample threshold
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RELIABILITY_PATH = PROJECT_ROOT / "src" / "reliability.py"
ML_DB_PATH = PROJECT_ROOT / "systems" / "MindLynx-Aistock" / "data" / "stock_analysis.db"


def load_backtest_summaries(min_samples: int = 30) -> dict[str, dict]:
    """Load per-stock sentiment accuracy from backtest_summaries (5d window)."""
    import sqlite3

    if not ML_DB_PATH.exists():
        print(f"ERROR: DB not found at {ML_DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(str(ML_DB_PATH))
    cur = conn.cursor()

    cur.execute("""
        SELECT code, completed_count, direction_accuracy_pct
        FROM backtest_summaries
        WHERE scope = 'stock' AND eval_window_days = 5
          AND direction_accuracy_pct IS NOT NULL
          AND completed_count >= ?
        ORDER BY code
    """, (min_samples,))

    results = {}
    for code, n, acc_pct in cur.fetchall():
        results[code] = {"n": n, "acc": acc_pct}

    conn.close()
    return results


def accuracy_to_alpha(acc: float) -> float:
    """Map sentiment direction accuracy to alpha value.

    Thresholds calibrated from 2026-06-07 backtest:
      acc >= 65% → 0.80 (high trust)
      acc >= 50% → 0.65 (default)
      acc >= 25% → 0.40 (reduced trust)
      acc < 25%  → 0.30 (low trust)
    """
    if acc >= 65.0:
        return 0.80
    elif acc >= 50.0:
        return 0.65
    elif acc >= 25.0:
        return 0.40
    else:
        return 0.30


def generate_override_dict(results: dict[str, dict]) -> dict[str, float]:
    """Generate new STOCK_ALPHA_OVERRIDE dict from backtest results."""
    overrides = {}
    for code, data in sorted(results.items()):
        alpha = accuracy_to_alpha(data["acc"])
        if abs(alpha - 0.65) > 0.01:  # only store if different from default
            overrides[code] = alpha
    return overrides


def update_reliability_file(overrides: dict[str, float], dry_run: bool = False) -> bool:
    """Rewrite STOCK_ALPHA_OVERRIDE section in reliability.py.

    Returns True if changes were made.
    """
    if not RELIABILITY_PATH.exists():
        print(f"ERROR: reliability.py not found at {RELIABILITY_PATH}")
        return False

    content = RELIABILITY_PATH.read_text(encoding="utf-8")

    # Build the new override block
    lines = ["    STOCK_ALPHA_OVERRIDE: dict[str, float] = {"]
    if overrides:
        for code, alpha in overrides.items():
            lines.append(f'        "{code}": {alpha},')
    lines.append("    }")
    new_block = "\n".join(lines)

    # Replace existing STOCK_ALPHA_OVERRIDE block
    pattern = r"    STOCK_ALPHA_OVERRIDE: dict\[str, float\] = \{.*?\n    \}"
    replacement = new_block

    if not re.search(pattern, content, re.DOTALL):
        print("ERROR: Could not find STOCK_ALPHA_OVERRIDE in reliability.py")
        return False

    new_content = re.sub(pattern, replacement, content, count=1, flags=re.DOTALL)

    if new_content == content:
        print("No changes needed.")
        return False

    if dry_run:
        print("\n--- DRY RUN — would apply ---")
        print(new_block)
        return True

    # 原子写入: 临时文件 → os.replace
    tmp = RELIABILITY_PATH.with_suffix(".py.tmp")
    tmp.write_text(new_content, encoding="utf-8")
    backup = RELIABILITY_PATH.with_suffix(".py.bak")
    if RELIABILITY_PATH.exists():
        RELIABILITY_PATH.rename(backup)
    os.replace(str(tmp), str(RELIABILITY_PATH))
    print(f"Backup saved to {backup}")
    return True


def print_report(results: dict[str, dict], overrides: dict[str, float]) -> None:
    """Print a human-readable calibration report."""
    print("\n" + "=" * 60)
    print("Alpha Calibration Report")
    print("=" * 60)
    print(f"{'Stock':<8} {'Samples':<8} {'Acc%':<8} {'Alpha':<8} {'Status':<10}")
    print("-" * 60)

    default = 0.65
    for code in sorted(results):
        data = results[code]
        alpha = overrides.get(code, default)
        status = "✓ override" if code in overrides else "(default)"
        print(f"{code:<8} {data['n']:<8} {data['acc']:<8.1f} {alpha:<8.2f} {status:<10}")

    non_default = sum(1 for c in results if c in overrides)
    print(f"\n{len(results)} stocks, {non_default} non-default overrides")
    print(f"Default alpha: {default}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate per-stock alpha from backtest data")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    parser.add_argument("--min-samples", type=int, default=30, help="Minimum samples per stock (default: 30)")
    args = parser.parse_args()

    results = load_backtest_summaries(min_samples=args.min_samples)
    if not results:
        print("No backtest data found. Run backtest first.")
        sys.exit(1)

    overrides = generate_override_dict(results)
    print_report(results, overrides)

    updated = update_reliability_file(overrides, dry_run=args.dry_run)
    if updated and not args.dry_run:
        print("\n✅ reliability.py updated. Next fusion run will use new alphas.")
    elif updated and args.dry_run:
        print("\nDry run complete. Run without --dry-run to apply.")
    else:
        print("\nNo changes to apply.")


if __name__ == "__main__":
    main()
