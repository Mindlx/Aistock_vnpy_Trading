"""Data source health monitor.

Tracks per-source hit rates, latency, and failure patterns.
Exposes a health_check() function for periodic monitoring.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SourceStats:
    name: str
    hits: int = 0
    misses: int = 0
    errors: int = 0
    total_latency_ms: float = 0.0
    first_seen: float = 0.0
    last_seen: float = 0.0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses + self.errors
        return self.hits / total if total > 0 else 0.0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.hits if self.hits > 0 else 0.0

    @property
    def health(self) -> str:
        if self.hit_rate >= 0.95:
            return "🟢"
        if self.hit_rate >= 0.70:
            return "🟡"
        return "🔴"


class DataHealthMonitor:
    """Tracks data source quality across the application lifetime."""

    def __init__(self):
        self._stats: dict[str, SourceStats] = defaultdict(lambda: SourceStats(name=""))
        self._lock = __import__("threading").Lock()

    def record(self, source: str, success: bool, latency_ms: float = 0.0) -> None:
        with self._lock:
            s = self._stats[source]
            s.name = source
            now = time.time()
            if not s.first_seen:
                s.first_seen = now
            s.last_seen = now
            if success:
                s.hits += 1
                s.total_latency_ms += latency_ms
            else:
                s.errors += 1

    def record_miss(self, source: str) -> None:
        with self._lock:
            s = self._stats[source]
            s.name = source
            s.misses += 1

    def snapshot(self) -> list[SourceStats]:
        with self._lock:
            return sorted(self._stats.values(), key=lambda s: s.hits, reverse=True)

    def report(self) -> str:
        stats = self.snapshot()
        if not stats:
            return "无数据源统计"
        lines = ["### 数据源健康报告", "| 来源 | 命中 | 成功率 | 平均延迟 | 状态 |", "|------|------|--------|----------|------|"]
        for s in stats:
            lines.append(f"| {s.name:20s} | {s.hits:4d} | {s.hit_rate*100:5.1f}% | {s.avg_latency_ms:6.0f}ms | {s.health} |")
        return "\n".join(lines)


# Global singleton
_health_monitor = DataHealthMonitor()


def record_data_fetch(source: str, success: bool, latency_ms: float = 0.0) -> None:
    _health_monitor.record(source, success, latency_ms)


def get_health_report() -> str:
    return _health_monitor.report()
