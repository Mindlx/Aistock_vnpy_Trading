"""Fundamental calibration — EP & ROE as validation signals.

Computes two fundamental quality indicators from realtime PE/PB data:
  EP  = 1/PE (盈利收益率, earnings yield)
  ROE = PB/PE (净资产收益率, via DuPont identity when data is reliable)

These are NOT added to the factor composite score. They serve as
calibration context for the LLM, answering:
  "Is this stock cheap or expensive relative to its earnings?"
  "Is this company actually profitable?"

Usage:
    from src.core.fundamental_calibration import compute_fundamental_calibration
    ctx = compute_fundamental_calibration(pe_ratio, pb_ratio)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def compute_fundamental_calibration(pe_ratio: float | None, pb_ratio: float | None) -> dict[str, Any]:
    if pe_ratio is None or pe_ratio == 0:
        return {"status": "insufficient_data", "warning": "PE 数据缺失"}

    result: dict[str, Any] = {"status": "ok"}

    if pe_ratio > 0:
        ep = round(1.0 / pe_ratio * 100, 2)
        result["ep"] = ep
        result["ep_label"] = _classify_ep(ep)

        if pb_ratio and pb_ratio > 0:
            roe = round(pb_ratio / pe_ratio * 100, 1)
            result["roe"] = roe
            result["roe_label"] = _classify_roe(roe)
    else:
        result["ep"] = None
        result["ep_label"] = "亏损"
        result["warning"] = "PE 为负，公司处于亏损状态"
        if pb_ratio and pb_ratio > 0:
            result["roe"] = None
            result["roe_label"] = "亏损(ROE为负)"

    return result


def _classify_ep(ep: float) -> str:
    if ep >= 8:
        return "高收益(EP≥8%)"
    elif ep >= 4:
        return "中等收益(4%≤EP<8%)"
    elif ep >= 2:
        return "低收益(2%≤EP<4%)"
    return "极低收益(EP<2%)"


def _classify_roe(roe: float) -> str:
    if roe >= 20:
        return "高ROE(≥20%)"
    elif roe >= 10:
        return "中等ROE(10%≤ROE<20%)"
    elif roe >= 0:
        return "低ROE(0%≤ROE<10%)"
    return "ROE为负"


def build_calibration_prompt(calibration: dict[str, Any], code: str, name: str) -> str:
    if calibration.get("status") != "ok":
        return ""

    lines = ["## 基本面校验"]
    if calibration.get("warning"):
        lines.append(f"⚠️ {calibration['warning']}")

    ep = calibration.get("ep")
    if ep is not None:
        lines.append(f"- EP(盈利收益率): {ep}% ({calibration['ep_label']})")
        lines.append("  含义: 每投入100元, 年化盈利约{:.1f}元".format(ep))

    roe = calibration.get("roe")
    if roe is not None:
        lines.append(f"- ROE(净资产收益率): {roe}% ({calibration['roe_label']})")

    ep_label = calibration.get("ep_label", "")
    lines.append(f"- 校验结论: {_infer_calibration_conclusion(ep_label, calibration.get('roe_label', ''))}")

    return "\n".join(lines)


def _infer_calibration_conclusion(ep_label: str, roe_label: str) -> str:
    positive = "高收益" in ep_label or "中等收益" in ep_label
    negative = "亏损" in ep_label or "ROE为负" in roe_label

    if negative:
        return "基本面偏弱。技术面信号需更严格验证，不宜重仓。"
    if positive and ("高ROE" in roe_label):
        return "基本面稳健。若技术面同步看多，可考虑中线持有。"
    if positive:
        return "估值合理但盈利质量中等。技术面信号为主，基本面辅助。"
    return "基本面信息不足，以技术面信号为准。"
