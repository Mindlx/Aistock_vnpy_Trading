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
