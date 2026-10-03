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
