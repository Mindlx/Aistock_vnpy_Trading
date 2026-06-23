"""Decision Signal Extractor.

Extracts structured signal records from agent opinions produced by the
SkillAgent / SkillAggregator pipeline.

Input format
------------
Each opinion dict should contain at minimum:
    agent_name  — e.g. "skill_agent.bull_trend" or "skill_consensus"
    signal      — "strong_buy" | "buy" | "hold" | "sell" | "strong_sell"
    confidence  — float 0.0-1.0
    reasoning   — evaluation text
    raw_data    — dict with optional keys:
        conditions_met     — list[str]
        conditions_missed  — list[str]
        score_adjustment   — float (-20..+20)

The extractor also accepts :class:`AgentOpinion` objects from
``src.agent.protocols``, which it converts via ``__dict__``.

Output format
-------------
Each output dict is a :class:`DecisionSignalCreate` compatible payload:
    stock_code, stock_name, skill_id, signal, confidence,
    conditions_met, conditions_missed, score_adjustment,
    reasoning, analysis_date, query_id
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# Agent name prefix → skill_id mapping.
# SkillAgent names follow the pattern "skill_agent.<skill_id>".
# The aggregator consensus uses "skill_consensus" or "strategy_consensus".
_SKILL_PREFIX = "skill_agent."
_CONSENSUS_NAMES = {"skill_consensus", "strategy_consensus"}


def _extract_skill_id(agent_name: str) -> str:
    """Derive a skill_id from an agent name.

    Priority:
    1. If the name starts with ``skill_agent.``, extract the suffix.
    2. If it is a consensus name, return ``"consensus"``.
    3. Otherwise use the agent_name as-is.
    """
    if agent_name in _CONSENSUS_NAMES:
        return "consensus"
    if agent_name.startswith(_SKILL_PREFIX):
        return agent_name[len(_SKILL_PREFIX):]
    return agent_name


def extract_signals_from_opinions(
    opinions: list[Any],
    stock_code: str,
    stock_name: str,
    query_id: str,
    analysis_date: datetime | None = None,
) -> list[dict[str, Any]]:
    """Extract structured signal dicts from a list of opinions.

    Accepts both :class:`AgentOpinion` dataclass instances and plain dicts.

    Args:
        opinions: List of AgentOpinion objects or dicts.
        stock_code: Stock identifier (e.g. ``"600519"``).
        stock_name: Human-readable stock name.
        query_id: Correlation ID for the analysis run.
        analysis_date: Override timestamp; defaults to ``datetime.now()``.

    Returns:
        List of dicts compatible with :class:`DecisionSignalCreate`.
    """
    if not opinions:
        return []

    if analysis_date is None:
        analysis_date = datetime.now()

    signals: list[dict[str, Any]] = []
    for opinion in opinions:
        # Normalize to dict
        if hasattr(opinion, "__dataclass_fields__"):
            op_dict = _dataclass_to_dict(opinion)
        elif isinstance(opinion, dict):
            op_dict = opinion
        else:
            logger.debug("Skipping non-opinion item: %s", type(opinion).__name__)
            continue

        agent_name = str(op_dict.get("agent_name", "") or "")
        if not agent_name:
            continue

        signal = str(op_dict.get("signal", "hold") or "hold")
        confidence = _safe_float(op_dict.get("confidence"), 0.0)
        reasoning = str(op_dict.get("reasoning", "") or "")

        # raw_data holds the full LLM output including conditions_met etc.
        raw_data = op_dict.get("raw_data")
        if not isinstance(raw_data, dict):
            raw_data = {}

        conditions_met = _safe_list(raw_data.get("conditions_met", []))
        conditions_missed = _safe_list(raw_data.get("conditions_missed", []))
        score_adjustment = _safe_float(raw_data.get("score_adjustment"), 0.0)

        skill_id = _extract_skill_id(agent_name)

        signals.append(
            {
                "stock_code": stock_code,
                "stock_name": stock_name,
                "skill_id": skill_id,
                "signal": signal,
                "confidence": confidence,
                "conditions_met": conditions_met,
                "conditions_missed": conditions_missed,
                "score_adjustment": score_adjustment,
                "reasoning": reasoning,
                "analysis_date": analysis_date,
                "query_id": query_id,
            }
        )

    return signals


def _dataclass_to_dict(obj: Any) -> dict[str, Any]:
    """Convert a dataclass instance to a dict, recursing into nested objects."""
    from dataclasses import fields

    result: dict[str, Any] = {}
    for field_def in fields(obj):
        value = getattr(obj, field_def.name)
        if hasattr(value, "__dataclass_fields__"):
            value = _dataclass_to_dict(value)
        result[field_def.name] = value
    return result


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _safe_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        return [value]
    return []
