"""c1test 报告解析回归测试 (mode 标签 + 权重网格正确/总数)。

修复背景 (2026-10-03):
- 无参运行实为全量, 但报告 mode 被标为 quick (generate_unified_report 误判 sys.argv)。
- 权重网格"最优"行原本只含准确率, 解析后 correct/total 恒为 0/0;
  backtest.py 最优行补打计数后此处校验解析。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import c1test


class TestReportMode:
    def test_default_run_is_full(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["c1test.py"])
        assert c1test.generate_unified_report({})["mode"] == "full"

    def test_quick_flag_is_quick(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["c1test.py", "--quick"])
        assert c1test.generate_unified_report({})["mode"] == "quick"


class TestWeightSweepParse:
    def _run_with_stdout(self, monkeypatch, stdout: str):
        class _Proc:
            returncode = 0
            stderr = ""

        _Proc.stdout = stdout
        monkeypatch.setattr(c1test.subprocess, "run", lambda *a, **k: _Proc())
        return c1test.phase7_weight_sweep()

    def test_parses_correct_and_total_from_best_line(self, monkeypatch):
        res = self._run_with_stdout(
            monkeypatch, "  ✅ 最优: (0.00, 0.55, 0.30) → 49.0% (612/1249)\n"
        )
        bw = res["best_weights"]
        assert bw["accuracy"] == 49.0
        assert bw["correct"] == 612
        assert bw["total"] == 1249

    def test_best_line_without_counts_is_backward_compatible(self, monkeypatch):
        res = self._run_with_stdout(
            monkeypatch, "  ✅ 最优: (0.30, 0.40, 0.30) → 46.4%\n"
        )
        bw = res["best_weights"]
        assert bw["accuracy"] == 46.4
        assert bw["correct"] == 0
        assert bw["total"] == 0


class TestMlProdDedup:
    """phase3_ml 生产对齐去重视图 (BUG-1 修复回归, 2026-10-03)."""

    @staticmethod
    def _view(rows):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE analysis_history "
            "(code TEXT, created_at TEXT, sentiment_score INTEGER, "
            " operation_advice TEXT, report_type TEXT)"
        )
        conn.executemany("INSERT INTO analysis_history VALUES (?,?,?,?,?)", rows)
        conn.execute(c1test.ML_PROD_VIEW_SQL)
        return conn

    def test_keeps_only_latest_per_code_day(self):
        conn = self._view([
            ("001", "2026-09-01 09:03", 40, "", "full"),
            ("001", "2026-09-01 15:04", 55, "", "full"),   # 同日最新 → 保留
            ("001", "2026-09-02 11:00", 60, "", "full"),
            ("002", "2026-09-01 10:00", 30, "", "full"),
            ("002", "2026-09-01 22:00", 35, "", "full"),   # 同日最新 → 保留
        ])
        got = conn.execute(
            "SELECT code, substr(created_at,1,10), sentiment_score FROM ml_prod "
            "ORDER BY code, created_at"
        ).fetchall()
        assert got == [
            ("001", "2026-09-01", 55),
            ("001", "2026-09-02", 60),
            ("002", "2026-09-01", 35),
        ]

    def test_null_sentiment_rows_excluded_and_not_selected_as_latest(self):
        conn = self._view([
            ("001", "2026-09-01 09:00", None, "", "full"),
            ("001", "2026-09-01 15:00", 50, "", "full"),
            ("001", "2026-09-01 22:00", None, "", "full"),  # 更晚但 NULL → 不参与
        ])
        got = conn.execute("SELECT sentiment_score FROM ml_prod").fetchall()
        assert got == [(50,)]

    def test_excludes_fusion_and_market_report_types(self):
        conn = self._view([
            ("001", "2026-09-01 11:04", 55, "", "full"),      # ML 行 → 保留
            ("001", "2026-09-01T11:00", 73, "", "fusion"),    # 字符串最大但非 ML → 排除
            ("MARKET", "2026-09-01 15:00", 60, "", "market_review"),  # → 排除
            ("002", "2026-09-01 15:00", 40, "", "simple"),    # ML(simple) → 保留
        ])
        got = conn.execute(
            "SELECT code, sentiment_score FROM ml_prod ORDER BY code"
        ).fetchall()
        assert got == [("001", 55), ("002", 40)]


class TestMlL7Mapping:
    """c1test 的 ML L7 映射必须与生产 SignalNormalizer 全值一致 (防再次漂移)."""

    def test_matches_production_normalizer_for_all_scores(self):
        from src.normalizer import SignalNormalizer
        mismatches = [
            (s, c1test.normalize_ml_sentiment_l7(s), SignalNormalizer.normalize_mindlynx_score(s))
            for s in range(0, 101)
            if c1test.normalize_ml_sentiment_l7(s) != SignalNormalizer.normalize_mindlynx_score(s)
        ]
        assert mismatches == [], f"与生产映射不一致: {mismatches[:5]}"
