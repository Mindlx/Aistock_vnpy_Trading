"""Factor weight automatic recalibration.

Recalibrates factor weights based on empirical IC computed from stock_daily
data. Triggered quarterly or when backtest_results grows by >= 50 entries.

Usage:
    python -m src.core.factor_recalibrate           # manual run
    from src.core.factor_recalibrate import FactorRecalibrator
    recal = FactorRecalibrator()
    report = recal.run()                             # returns recalibration report
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np

from src.config import get_config
from src.core.factor_engine import (
    CORE_FACTORS,
    FactorEngine,
)

logger = logging.getLogger(__name__)

_LAST_RUN_FILE = "reports/factors/.last_recalibrate.json"
_MIN_BACKTEST_GROWTH = 50
_MIN_FACTOR_RECORDS = 30


def _spearman_ic(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman rank correlation between two arrays."""
    if len(x) < 2 or len(y) < 2:
        return 0.0
    x_rank = np.argsort(np.argsort(x)).astype(float)
    y_rank = np.argsort(np.argsort(y)).astype(float)
    if np.std(x_rank) < 1e-10 or np.std(y_rank) < 1e-10:
        return 0.0
    n = len(x_rank)
    d = x_rank - y_rank
    rho = 1.0 - (6.0 * np.sum(d**2)) / (n * (n**2 - 1.0))
    return float(rho) if not np.isnan(rho) else 0.0


def _load_daily_data(db_path: str, code: str, start_date: str, end_date: str) -> list[dict]:
    """Load OHLCV data for a stock within date range."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT date, close, volume, high, low, pct_chg FROM stock_daily "
        "WHERE code=? AND date BETWEEN ? AND ? ORDER BY date",
        (code, start_date, end_date),
    ).fetchall()
    conn.close()
    return [dict(zip(["date", "close", "volume", "high", "low", "pct_chg"], r, strict=False)) for r in rows]


def _get_backtest_results_count(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    cnt = conn.execute("SELECT COUNT(*) FROM backtest_results").fetchone()[0]
    conn.close()
    return cnt


@dataclass
class FactorIC:
    name: str
    display_name: str
    effective_ic: float
    sample_count: int
    higher_better: bool
    old_weight: float
    new_weight: float


class FactorRecalibrator:
    """Compute per-factor effective IC and recalibrate composite weights.

    Walks through historical stock_daily data for every tracked stock,
    computes factor values at each rolling window, and compares against
    forward returns to derive per-factor predictive power (Spearman IC).
    Weights are re-normalized using positive effective IC only.

    If a factor's effective IC is in the wrong direction (e.g. positive
    IC for a `higher_better=False` factor), its weight is set to 0.
    """

    def __init__(self, db_path: str = "data/stock_analysis.db"):
        self.db_path = db_path
        self.lookback_days = 60
        self.forward_days = 5
        self.step_days = 5

    def should_recalibrate(self) -> tuple[bool, str]:
        """Check whether recalibration should be triggered.

        Triggers when:
        - backtest_results has grown by >= 100 since last run, OR
        - Last run was in a different calendar quarter.

        Returns (should_run, reason).
        """
        last_run = self._load_last_run()
        backtest_count = _get_backtest_results_count(self.db_path)

        if last_run:
            prev_count = last_run.get("backtest_count", 0)
            growth = backtest_count - prev_count
            if growth >= _MIN_BACKTEST_GROWTH:
                return True, f"backtest_results grew by {growth} (>= {_MIN_BACKTEST_GROWTH})"

            last_date = last_run.get("last_run", "")
            if len(last_date) >= 7:
                last_quarter = (int(last_date[:4]), (int(last_date[5:7]) - 1) // 3)
                now = datetime.now()
                now_quarter = (now.year, (now.month - 1) // 3)
                if now_quarter != last_quarter:
                    return True, f"quarter changed ({last_quarter} → {now_quarter})"
        elif backtest_count >= _MIN_BACKTEST_GROWTH:
            return True, f"first run with {backtest_count} backtest results (>= {_MIN_BACKTEST_GROWTH})"

        return False, f"not triggered (backtest={backtest_count}, threshold={_MIN_BACKTEST_GROWTH})"

    def run(self) -> dict[str, Any]:
        """Run recalibration: compute per-factor IC and update weights.

        Returns a report dict with old/new weights, IC values, and metadata.
        """
        logger.info("Starting factor weight recalibration...")

        conn = sqlite3.connect(self.db_path)
        codes = [r[0] for r in conn.execute("SELECT DISTINCT code FROM stock_daily ORDER BY code").fetchall()]
        date_row = conn.execute("SELECT MIN(date), MAX(date) FROM stock_daily").fetchone()
        conn.close()

        if not date_row or not date_row[0]:
            return {"error": "no stock_daily data"}

        min_date, max_date = str(date_row[0]), str(date_row[1])
        logger.info("Loaded %d stocks, date range %s → %s", len(codes), min_date, max_date)

        engine = FactorEngine(factors=CORE_FACTORS)
        factor_records: dict[str, list[tuple[float, float]]] = {fd.name: [] for fd in CORE_FACTORS}

        for code in codes:
            all_data = _load_daily_data(self.db_path, code, min_date, max_date)
            required_rows = self.lookback_days + self.forward_days
            if len(all_data) < required_rows:
                continue

            for start in range(0, len(all_data) - required_rows + 1, self.step_days):
                lookback = all_data[start : start + self.lookback_days]
                forward_start = start + self.lookback_days
                forward_end = forward_start + self.forward_days
                if forward_end >= len(all_data):
                    break

                forward_return = (all_data[forward_end]["close"] - all_data[forward_start]["close"]) / all_data[
                    forward_start
                ]["close"]

                result = engine.compute_for_stock(code, lookback)
                for fd in CORE_FACTORS:
                    raw_val = result.raw_factors.get(fd.name, 0.0)
                    adjusted = raw_val if fd.higher_better else -raw_val
                    factor_records[fd.name].append((adjusted, forward_return))

        logger.info(
            "Recorded factor values: %s",
            {k: len(v) for k, v in factor_records.items()},
        )

        ic_results: list[FactorIC] = []
        for fd in CORE_FACTORS:
            records = factor_records.get(fd.name, [])
            if len(records) < _MIN_FACTOR_RECORDS:
                logger.warning(
                    "Factor %s: insufficient records (%d < %d), keeping old weight",
                    fd.name,
                    len(records),
                    _MIN_FACTOR_RECORDS,
                )
                ic_results.append(
                    FactorIC(
                        name=fd.name,
                        display_name=fd.display_name,
                        effective_ic=0.0,
                        sample_count=len(records),
                        higher_better=fd.higher_better,
                        old_weight=fd.weight,
                        new_weight=fd.weight,
                    )
                )
                continue

            vals = np.array([r[0] for r in records], dtype=float)
            fwd = np.array([r[1] for r in records], dtype=float)
            ic = _spearman_ic(vals, fwd)

            ic_results.append(
                FactorIC(
                    name=fd.name,
                    display_name=fd.display_name,
                    effective_ic=round(ic, 6),
                    sample_count=len(records),
                    higher_better=fd.higher_better,
                    old_weight=fd.weight,
                    new_weight=0.0,
                )
            )

        total_positive_ic = sum(max(0, r.effective_ic) for r in ic_results)

        if total_positive_ic < 1e-8:
            logger.warning("All factors have zero or negative IC; keeping old weights")
            for r in ic_results:
                r.new_weight = r.old_weight
        else:
            for r in ic_results:
                if r.effective_ic > 0 and r.sample_count >= _MIN_FACTOR_RECORDS:
                    r.new_weight = round(r.effective_ic / total_positive_ic, 4)
                else:
                    r.new_weight = 0.0

            if all(r.new_weight <= 0 for r in ic_results):
                logger.warning("All weights zero after recalibration; falling back to uniform")
                n_active = sum(1 for r in ic_results if r.sample_count >= _MIN_FACTOR_RECORDS)
                if n_active > 0:
                    for r in ic_results:
                        if r.sample_count >= _MIN_FACTOR_RECORDS:
                            r.new_weight = round(1.0 / n_active, 4)

        for r in ic_results:
            for fd in CORE_FACTORS:
                if fd.name == r.name:
                    fd.weight = r.new_weight
                    fd.ic = r.effective_ic
                    break

        self._save_last_run(_get_backtest_results_count(self.db_path))

        report = self._build_report(ic_results)
        logger.info("Recalibration complete. See report for details.")
        return report

    def generate_report(self, output_path: str | None = None) -> str | None:
        """Run recalibration and save report to a JSON file.

        Args:
            output_path: Path for the JSON report. Defaults to
                reports/factors/recalibration_report_YYYYMMDD.json.

        Returns:
            Path to the saved report, or None if failed.
        """
        report = self.run()
        if "error" in report:
            logger.error("Recalibration failed: %s", report["error"])
            return None

        if output_path is None:
            cfg = get_config()
            output_path = os.path.join(
                cfg.factor_monitor_output_dir,
                f"recalibration_report_{datetime.now().strftime('%Y%m%d')}.json",
            )

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        logger.info("Recalibration report saved to %s", output_path)
        return output_path

    def _load_last_run(self) -> dict[str, Any]:
        if not os.path.exists(_LAST_RUN_FILE):
            return {}
        try:
            with open(_LAST_RUN_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load last-run state: %s", e)
            return {}

    def _save_last_run(self, backtest_count: int) -> None:
        data = {
            "last_run": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "backtest_count": backtest_count,
        }
        os.makedirs(os.path.dirname(_LAST_RUN_FILE), exist_ok=True)
        with open(_LAST_RUN_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _build_report(self, ic_results: list[FactorIC]) -> dict[str, Any]:
        now = datetime.now()
        factors_data = []
        for r in ic_results:
            weight_change_pct = 0.0
            if r.old_weight > 0:
                weight_change_pct = round((r.new_weight - r.old_weight) / r.old_weight * 100, 1)

            factors_data.append(
                {
                    "name": r.name,
                    "display_name": r.display_name,
                    "effective_ic": r.effective_ic,
                    "sample_count": r.sample_count,
                    "higher_better": r.higher_better,
                    "old_weight": r.old_weight,
                    "new_weight": r.new_weight,
                    "weight_change_pct": weight_change_pct,
                }
            )

        large_changes = [
            f for f in factors_data if abs(f["weight_change_pct"]) > 20 and f["sample_count"] >= _MIN_FACTOR_RECORDS
        ]

        return {
            "report_type": "factor_weight_recalibration",
            "generated_at": now.isoformat(),
            "backtest_results_count": _get_backtest_results_count(self.db_path),
            "min_records_per_factor": _MIN_FACTOR_RECORDS,
            "factors": factors_data,
            "large_changes": large_changes,
            "has_large_changes": len(large_changes) > 0,
        }


def auto_recalibrate_if_needed(
    db_path: str = "data/stock_analysis.db",
) -> dict[str, Any] | None:
    """Check if recalibration is needed and run if so.

    Returns the recalibration report dict if ran, None if skipped.
    """
    cfg = get_config()
    if not cfg.factor_monitor_enabled:
        logger.info("FactorMonitor disabled; skipping recalibration check")
        return None

    recal = FactorRecalibrator(db_path=db_path)
    should_run, reason = recal.should_recalibrate()
    if not should_run:
        logger.debug("Skipping recalibration: %s", reason)
        return None

    logger.info("Triggering recalibration: %s", reason)
    return recal.run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    recal = FactorRecalibrator()
    report = recal.generate_report()
    if report is None:
        print("Recalibration failed — see log for details")  # noqa: T201
    else:
        print(f"Report saved to: {report}")  # noqa: T201
        print("\n=== 因子权重重校准报告 ===")  # noqa: T201
        print("因子名称          | 有效IC   | 样本数   | 旧权重 | 新权重 | 变化")  # noqa: T201
        print("-" * 68)  # noqa: T201
        for f in CORE_FACTORS:
            chg = (f.weight - f.weight) / f.weight * 100 if f.weight > 0 else 0
            print(  # noqa: T201
                f"{f.display_name:12s}  | {f.ic:+.4f}  | {0:>6d}  | {f.weight:.3f}  | {f.weight:.3f}  | {chg:+.0f}%"
            )
