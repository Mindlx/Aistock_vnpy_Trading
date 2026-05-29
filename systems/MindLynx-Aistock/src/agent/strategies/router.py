"""Compatibility wrapper for the legacy strategy router import path."""

from src.agent.skills.router import _DEFAULT_SKILLS, _DEFAULT_STRATEGIES, SkillRouter, StrategyRouter

__all__ = ["SkillRouter", "StrategyRouter", "_DEFAULT_SKILLS", "_DEFAULT_STRATEGIES"]
