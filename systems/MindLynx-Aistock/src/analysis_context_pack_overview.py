"""
===================================
Analysis Context Pack — Overview Formatter
===================================

Generates a compact human-readable summary of an ``AnalysisSnapshot``
for logging, notifications, or debug output.

Example output::

    600519 · 盘中阶段 | MA5=148.2 MA10=146.8 多头排列 | PE=32.5 PB=6.8 ROE=21.0% | 正向情绪 | 筹码集中
"""

from __future__ import annotations

from src.schemas.analysis_context_pack import AnalysisSnapshot


def format_context_summary(snapshot: AnalysisSnapshot) -> str:
    """
    Return a 1-paragraph human-readable summary of the snapshot.

    The summary includes:
    - Stock code + market session label
    - Key MA values and MA alignment status
    - PE / PB / ROE (when available)
    - Sentiment polarity description
    - Chip concentration (when available)

    Parameters
    ----------
    snapshot : AnalysisSnapshot
        The structured snapshot to summarise.

    Returns
    -------
    str
        A single-line summary string suitable for logging.
    """
    parts: list[str] = []

    # --- identity + market phase ---
    code = snapshot.stock_code
    name = snapshot.stock_name
    label = snapshot.market_phase.session_label if snapshot.market_phase.session_label else ""

    identity = code
    if name:
        identity = f"{name}({code})" if code else name
    if label:
        identity = f"{identity} · {label}阶段"
    parts.append(identity)

    # --- technical ---
    tech_parts: list[str] = []
    t = snapshot.technical
    if t.ma5 is not None:
        tech_parts.append(f"MA5={t.ma5}")
    if t.ma10 is not None:
        tech_parts.append(f"MA10={t.ma10}")
    if t.ma20 is not None:
        tech_parts.append(f"MA20={t.ma20}")
    if t.volume_ratio is not None:
        tech_parts.append(f"量比={t.volume_ratio}")
    if t.ma_status:
        tech_parts.append(t.ma_status)

    if tech_parts:
        parts.append(" | ".join(tech_parts))

    # --- fundamental ---
    fund_parts: list[str] = []
    f = snapshot.fundamental
    if f.pe is not None:
        fund_parts.append(f"PE={f.pe}")
    if f.pb is not None:
        fund_parts.append(f"PB={f.pb}")
    if f.roe is not None:
        fund_parts.append(f"ROE={f.roe}%")
    if f.market_cap is not None:
        # Display in billions if large enough
        cap_b = f.market_cap / 1e8
        fund_parts.append(f"市值={cap_b:.1f}亿" if cap_b >= 1 else f"市值={f.market_cap:.0f}")

    if fund_parts:
        parts.append(" | ".join(fund_parts))

    # --- sentiment ---
    s = snapshot.sentiment
    if s.news_count > 0:
        if s.news_sentiment >= 0.65:
            sent_label = "正向情绪"
        elif s.news_sentiment <= 0.35:
            sent_label = "负面情绪"
        else:
            sent_label = "中性情绪"
        parts.append(f"{sent_label}({s.news_count}条)")

    # --- chip ---
    c = snapshot.chip
    if c.concentration:
        parts.append(f"筹码{c.concentration}")
    elif c.chip_avg_cost is not None:
        parts.append(f"筹码成本={c.chip_avg_cost}")

    return " | ".join(parts)
