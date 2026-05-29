"""
Compatibility re-exports for the legacy strategy namespace.

Provides:
- :class:`StrategyAgent` — legacy alias of :class:`SkillAgent`
- :class:`StrategyRouter` — legacy alias of :class:`SkillRouter`
- :class:`StrategyAggregator` — legacy alias of :class:`SkillAggregator`
"""

from src.agent.strategies.aggregator import StrategyAggregator
from src.agent.strategies.router import StrategyRouter
from src.agent.strategies.strategy_agent import StrategyAgent

__all__ = [
    "StrategyAgent",
    "StrategyRouter",
    "StrategyAggregator",
]
