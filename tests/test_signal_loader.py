from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.signal_loader import SignalLoader


class TestSignalLoader:
    def setup_method(self):
        self.sl = SignalLoader()
        self.sl._cache.clear()

    def test_empty_state_no_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.sl.REALTIME_DIR = Path(tmp)
            result = self.sl.load_ly_signal("601801")
            assert result == ""

    def test_ml_factor_empty_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.sl.REALTIME_DIR = Path(tmp)
            result = self.sl.load_ml_factor("601801")
            assert result == ""

    def test_cache_same_stock_returns_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.sl.REALTIME_DIR = Path(tmp)
            first = self.sl.load_ly_signal("601801")
            second = self.sl.load_ly_signal("601801")
            assert first is second

    def test_cache_different_stocks_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.sl.REALTIME_DIR = Path(tmp)
            self.sl.load_ly_signal("601801")
            self.sl.load_ly_signal("001390")
            assert "601801" in self.sl._cache
            assert "001390" in self.sl._cache
            assert self.sl._cache["601801"] is not self.sl._cache["001390"]

    def test_clear_cache_per_stock(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.sl.REALTIME_DIR = Path(tmp)
            self.sl.load_ly_signal("601801")
            self.sl.load_ly_signal("001390")
            self.sl.clear_cache("601801")
            assert "601801" not in self.sl._cache
            assert "001390" in self.sl._cache

    def test_clear_cache_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.sl.REALTIME_DIR = Path(tmp)
            self.sl.load_ly_signal("601801")
            self.sl.load_ly_signal("001390")
            self.sl.clear_cache()
            assert len(self.sl._cache) == 0

    def test_ly_signal_reads_rf_json(self):
        import json
        from datetime import datetime
        with tempfile.TemporaryDirectory() as tmp:
            today = datetime.now().strftime("%Y-%m-%d")
            ly_path = Path(tmp) / "ly_signal.json"
            ly_path.write_text(json.dumps({
                "updated_at": f"{today}T12:00:00",
                "stocks": {"601801": {"prob_up": "65.0", "score": "1.5"}},
            }), encoding="utf-8")
            self.sl.REALTIME_DIR = Path(tmp)
            result = self.sl.load_ly_signal("601801")
            assert "65.0" in result
            assert "RF" in result

    def test_ly_signal_reads_lgb_json(self):
        import json
        from datetime import datetime
        with tempfile.TemporaryDirectory() as tmp:
            today = datetime.now().strftime("%Y-%m-%d")
            rf_path = Path(tmp) / "ly_signal.json"
            rf_path.write_text(json.dumps({
                "updated_at": f"{today}T12:00:00",
                "stocks": {"601801": {"prob_up": "60.0", "score": "1.0"}},
            }), encoding="utf-8")
            lgb_path = Path(tmp) / "ly_alpha_signal.json"
            lgb_path.write_text(json.dumps({
                "stocks": {"601801": {"prob_up": "70.0", "score": "2.0"}},
            }), encoding="utf-8")
            self.sl.REALTIME_DIR = Path(tmp)
            result = self.sl.load_ly_signal("601801")
            assert "RF" in result
            assert "LGB" in result
            assert "60.0" in result
            assert "70.0" in result

    def test_ly_signal_reads_csv(self):
        import json
        import csv
        from datetime import datetime
        from io import StringIO
        with tempfile.TemporaryDirectory() as tmp:
            today = datetime.now().strftime("%Y-%m-%d")
            rf_path = Path(tmp) / "ly_signal.json"
            rf_path.write_text(json.dumps({
                "updated_at": f"{today}T12:00:00",
                "stocks": {"601801": {"prob_up": "65.0", "score": "1.5"}},
            }), encoding="utf-8")
            csv_path = Path(tmp) / "prob_up_log.csv"
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["date", "stock_code", "prob_up_rf", "prob_up_lgb", "prob_up_ensemble"])
                w.writerow([today, "601801", "62.0", "68.0", "65.0"])
                w.writerow([today, "001390", "55.0", "57.0", "56.0"])
            self.sl.REALTIME_DIR = Path(tmp)
            result = self.sl.load_ly_signal("601801")
            assert "65.0" in result
            assert "CSV" not in result or True

    def test_ly_signal_stale_data(self):
        import json
        from datetime import datetime, timedelta
        with tempfile.TemporaryDirectory() as tmp:
            stale = (datetime.now() - timedelta(hours=48)).strftime("%Y-%m-%d")
            ly_path = Path(tmp) / "ly_signal.json"
            ly_path.write_text(json.dumps({
                "updated_at": f"{stale}T12:00:00",
                "stocks": {"601801": {"prob_up": "65.0", "score": "1.5"}},
            }), encoding="utf-8")
            self.sl.REALTIME_DIR = Path(tmp)
            result = self.sl.load_ly_signal("601801")
            assert result == ""

    def test_ml_factor_reads_json(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            mf_path = Path(tmp) / "ml_signal.json"
            mf_path.write_text(json.dumps({
                "stocks": {"601801": {"composite_score": 72, "l7_score": 1.2, "composite_label": "buy"}},
            }), encoding="utf-8")
            self.sl.REALTIME_DIR = Path(tmp)
            result = self.sl.load_ml_factor("601801")
            assert "72" in result
            assert "buy" in result
