"""
Shared prompt fragments used across multiple system prompts.

Single source of truth for content that appears identically in
analyzer.py, executor.py, and decision_agent.py.  Edit here to
update everywhere.

Ref: docs/cross_layer_audit_lessons.md (ARCH-3 fix)

Calibration note (2026-06-07):
  Thresholds aligned with backtest-validated 52/49 split:
  - score >= 52 → bullish (74.6% accuracy, 98% coverage)
  - score <= 49 → bearish (92.8% accuracy for 31-49 range)
  - score 50-51 → flat zone (extremely narrow, only 2% of samples)

  Asymmetry warning: bearish signals are significantly more reliable
  than bullish signals in current market regime (downtrend_mid_vol).
  - 31-49 range: 92.8% direction accuracy
  - 52-59 range: 56.2% direction accuracy
  - 60-79 range: 38.2% direction accuracy (extreme scores LESS reliable!)
"""

from src.core.prompt_config import get_scoring_criteria

SCORING_CRITERIA = get_scoring_criteria()

from src.core.prompt_config import get_action_guardrails

ACTION_GUARDRAILS = get_action_guardrails()
