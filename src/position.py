"""
统一仓位模型 — 以总资金%为唯一参考系 (v1.0)

核心原则:
  - 所有内部计算用 **总资金的百分比** (0~100)
  - 推送显示: "仓位8% (轻仓)" — 自解释，无需"成"转换
  - 用户理解: "建议你用总资金的X%买这只股票"

兼容旧版:
  - pct_to_cheng() / cheng_to_pct() 专门处理历史数据
  - 旧日志中的"成"仍可正确渲染
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ══════════════════════════════════════════════
# L7 v4.0: 百分比仓位映射（替代旧版"成"映射）
# ══════════════════════════════════════════════

# 每档仓位范围 (min_pct, max_pct)，单位：% of 总资金
# 按 10只股票组合校准，单只上限25%
L7_TARGET_PCT_RANGE: dict[str, tuple[float, float]] = {
    "strong_bullish":   (20, 30),   # 重仓
    "bullish":          (10, 20),   # 中仓
    "cautious_bullish":  (5, 10),   # 轻仓
    "neutral":           (0, 0),    # 空仓/不动
    "cautious_bearish":  (0, 5),    # 轻仓/减仓
    "bearish":           (0, 3),    # 观察仓
    "strong_bearish":    (0, 0),    # 清仓
}

# 每档的精确目标值（用于计算）
L7_TARGET_PCT: dict[str, float] = {
    "strong_bullish":   25.0,    # (20+30)/2
    "bullish":          15.0,    # (10+20)/2
    "cautious_bullish":  7.5,    # (5+10)/2
    "neutral":           0.0,    # 空仓
    "cautious_bearish":  2.5,    # (0+5)/2
    "bearish":           1.5,    # (0+3)/2
    "strong_bearish":    0.0,    # 清仓
}

# 仓位标签（中文）
L7_TARGET_LABEL: dict[str, str] = {
    "strong_bullish":   "重仓",
    "bullish":          "中仓",
    "cautious_bullish": "轻仓",
    "neutral":          "空仓",
    "cautious_bearish": "轻仓",
    "bearish":          "观察仓",
    "strong_bearish":   "清仓",
}


# ══════════════════════════════════════════════
# 仓位标签枚举
# ══════════════════════════════════════════════

class PositionLabel(Enum):
    """标准化仓位标签——所有子系统统一。"""
    HEAVY = ("重仓", 20)       # >= 20%
    MEDIUM = ("中仓", 10)      # >= 10%
    LIGHT = ("轻仓", 5)        # >= 5%
    WATCH = ("观察仓", 2)      # >= 2%
    NONE = ("空仓", 0)         # < 2%

    @property
    def display(self) -> str:
        return self.value[0]

    @property
    def threshold(self) -> int:
        return self.value[1]


def pct_to_label(pct: float) -> PositionLabel:
    """百分比 → 仓位标签"""
    for label in PositionLabel:
        if pct >= label.threshold:
            return label
    return PositionLabel.NONE


# ══════════════════════════════════════════════
# 统一仓位数据模型
# ══════════════════════════════════════════════

@dataclass
class UnifiedPosition:
    """单只股票的仓位建议——以总资金%为唯一参考系。

    pct = 8.0  → 建议用总资金的 8% 持有这只股票
    不依赖用户当前持仓量，不涉及"成"单位。
    """
    pct: float                     # 建议仓位 % of 总资金 (0~100)
    label: str = "空仓"            # "重仓/中仓/轻仓/观察仓/空仓/清仓"
    min_pct: float = 0.0           # 范围下限
    max_pct: float = 0.0           # 范围上限
    source: str = "fusion"         # 来源: fusion / ml / at / ly

    def display(self) -> str:
        """推送显示: '仓位8% (轻仓)'"""
        if self.pct <= 0.5:
            return "空仓"
        return f"{self.pct:.0f}% ({self.label})"

    def display_detail(self, current_pct: float = 0.0) -> str:
        """详细显示: '仓位8% (轻仓) | 当前3% | 建议+5%'"""
        if self.pct <= 0.5:
            return "空仓"
        detail = f"{self.pct:.0f}% ({self.label})"
        if current_pct > 0:
            delta = self.pct - current_pct
            if abs(delta) >= 1:
                direction = "+" if delta > 0 else ""
                detail += f" | 当前{current_pct:.0f}% 建议{direction}{delta:.0f}%"
        return detail

    @classmethod
    def from_signal(cls, signal_label: str, source: str = "fusion") -> UnifiedPosition:
        """从 L7 信号标签创建仓位建议。"""
        target = L7_TARGET_PCT.get(signal_label, 0.0)
        label = L7_TARGET_LABEL.get(signal_label, "空仓")
        min_p, max_p = L7_TARGET_PCT_RANGE.get(signal_label, (0, 0))
        return cls(pct=target, label=label, min_pct=min_p, max_pct=max_p, source=source)

    @classmethod
    def from_pct(cls, pct: float, source: str = "fusion") -> UnifiedPosition:
        """从百分比值创建仓位建议。"""
        label = pct_to_label(pct).display
        return cls(pct=round(pct, 1), label=label, min_pct=pct, max_pct=pct, source=source)


# ══════════════════════════════════════════════
# 仓位约束引擎（组合级别）
# ══════════════════════════════════════════════

class PositionConstraintEngine:
    """组合级别仓位约束。

    确保所有股票总仓位不超过 100%，单只不超过 25%。
    分歧状态下单只不超过 10%。
    """

    MAX_SINGLE_PCT = 25.0       # 单只上限
    MAX_TOTAL_PCT = 95.0        # 总仓位上限（留5%现金）
    DISAGREEMENT_CAP = 10.0     # 分歧状态单只上限

    def __init__(self, total_stocks: int = 10):
        self.total_stocks = total_stocks

    def apply(
        self,
        positions: list[UnifiedPosition],
        disagreements: Optional[list[bool]] = None,
    ) -> list[UnifiedPosition]:
        """应用仓位约束，按比例缩放。

        Args:
            positions: 每只股票的仓位建议
            disagreements: 每只股票是否有分歧（可选）
        Returns:
            约束后的仓位建议
        """
        if not positions:
            return positions

        # 复制一份避免修改原始数据
        result = []
        for i, pos in enumerate(positions):
            p = UnifiedPosition(
                pct=pos.pct, label=pos.label,
                min_pct=pos.min_pct, max_pct=pos.max_pct,
                source=pos.source,
            )
            # 分歧状态硬上限
            if disagreements and i < len(disagreements) and disagreements[i]:
                if p.pct > self.DISAGREEMENT_CAP:
                    p.pct = self.DISAGREEMENT_CAP
                    p.label = pct_to_label(p.pct).display
            # 单只上限
            if p.pct > self.MAX_SINGLE_PCT:
                p.pct = self.MAX_SINGLE_PCT
                p.label = pct_to_label(p.pct).display
            result.append(p)

        # 总仓位上限 → 按比例缩放
        total = sum(p.pct for p in result)
        if total > self.MAX_TOTAL_PCT:
            scale = self.MAX_TOTAL_PCT / total
            for p in result:
                p.pct = round(p.pct * scale, 1)
                p.label = pct_to_label(p.pct).display

        return result

    def summary(self, positions: list[UnifiedPosition]) -> dict:
        """生成组合仓位摘要。"""
        active = [p for p in positions if p.pct > 0.5]
        total = sum(p.pct for p in active)
        return {
            "total_position_pct": round(total, 1),
            "active_stocks": len(active),
            "cash_pct": round(max(0, 100 - total), 1),
        }

    def summary_display(self, positions: list[UnifiedPosition]) -> str:
        """组合仓位摘要显示: '总仓位45% (5只活跃)'"""
        s = self.summary(positions)
        label = pct_to_label(s["total_position_pct"]).display
        return f"总仓位{s['total_position_pct']:.0f}% ({label})"

    @staticmethod
    def portfolio_allocate(
        positions: list[UnifiedPosition],
        total_capital: float,
    ) -> list[dict]:
        """将百分比仓位转换为实际股数（按A股100股/手）。"""
        results = []
        for pos in positions:
            if pos.pct <= 0.5:
                results.append({"pct": 0, "shares": 0, "lots": 0})
                continue
            capital_amount = total_capital * pos.pct / 100
            # 此处需要股价才能算股数，仅示意
            results.append({"pct": pos.pct, "capital_amount": capital_amount})
        return results


# ══════════════════════════════════════════════
# "成" ↔ "%" 转换（仅向后兼容用）
# ══════════════════════════════════════════════

def pct_to_cheng(pct: float) -> str:
    """% → '成' 显示（历史兼容）。1成 = 10%"""
    if pct <= 0.5:
        return "0成"
    cheng = pct / 10
    if cheng >= 2:
        return f"{cheng:.0f}成"
    return f"{cheng:.1f}成"


def cheng_to_pct(text: str) -> float:
    """解析旧版'成'文本 → %。"""
    if not text:
        return 0.0
    if "清仓" in text or "0成" in text:
        return 0.0
    if "大幅减仓" in text:
        return 3.0
    if "减仓至" in text or "以内" in text:
        m = re.search(r"([\d.]+)成", text)
        return float(m.group(1)) * 10 if m else 5.0
    if "-" in text:
        parts = text.replace("成", "").split("-")
        low, high = float(parts[0]), float(parts[1])
        return (low + high) / 2 * 10
    m = re.search(r"([\d.]+)成", text)
    return float(m.group(1)) * 10 if m else 0.0
