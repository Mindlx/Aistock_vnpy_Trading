"""Provider call tracking for LLM completion attempts.

Records each provider/model attempt made during LLM completion calls,
including success status, latency, and failure reasons. Used to surface
provider reliability and fallback behaviour.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProviderTraceEntry:
    """A single LLM provider attempt."""

    provider: str
    model: str
    timestamp: float
    success: bool
    latency_ms: float
    reason: str = ""


@dataclass
class ProviderTrace:
    """Tracks all provider attempts for a single completion request."""

    entries: list[ProviderTraceEntry] = field(default_factory=list)
    total_retries: int = 0

    def record_entry(
        self,
        provider: str,
        model: str,
        success: bool,
        latency_ms: float,
        reason: str = "",
    ) -> None:
        """Record a single provider attempt."""
        if len(self.entries) > 0:
            self.total_retries += 1
        self.entries.append(
            ProviderTraceEntry(
                provider=provider,
                model=model,
                timestamp=time.time(),
                success=success,
                latency_ms=latency_ms,
                reason=reason,
            )
        )

    def get_summary(self) -> dict[str, Any]:
        """Return an aggregate summary of all recorded entries."""
        total_calls = len(self.entries)
        if total_calls == 0:
            return {
                "total_calls": 0,
                "total_retries": self.total_retries,
                "success_rate": 0.0,
                "avg_latency_ms": 0.0,
                "by_provider": {},
            }

        success_count = sum(1 for e in self.entries if e.success)
        total_latency = sum(e.latency_ms for e in self.entries)

        by_provider: dict[str, dict[str, Any]] = {}
        for entry in self.entries:
            p = entry.provider
            if p not in by_provider:
                by_provider[p] = {"calls": 0, "successes": 0, "total_latency_ms": 0.0}
            by_provider[p]["calls"] += 1
            if entry.success:
                by_provider[p]["successes"] += 1
            by_provider[p]["total_latency_ms"] += entry.latency_ms

        for p in by_provider:
            info = by_provider[p]
            info["success_rate"] = info["successes"] / info["calls"]
            info["avg_latency_ms"] = round(info["total_latency_ms"] / info["calls"], 1)

        return {
            "total_calls": total_calls,
            "total_retries": self.total_retries,
            "success_rate": round(success_count / total_calls, 4),
            "avg_latency_ms": round(total_latency / total_calls, 1),
            "by_provider": by_provider,
        }
