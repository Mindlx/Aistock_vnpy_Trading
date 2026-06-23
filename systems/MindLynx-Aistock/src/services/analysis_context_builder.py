"""
===================================
AnalysisContextBuilder — Builds structured AnalysisSnapshot
===================================

Constructs a validated ``AnalysisSnapshot`` from the raw pipeline data
(enhanced_context, news_content, realtime_quote, chip_data, factor_zscores).

Each sub-builder is wrapped in try/except with logging so that a single
sub-context failure never breaks the entire snapshot.

Usage::

    builder = AnalysisContextBuilder()
    snapshot = builder.build_snapshot(
        enhanced_context=enhanced_context,
        news_content=news_context,
        realtime_quote=realtime_quote,
        chip_data=chip_data,
        factor_zscores=getattr(self, "_factor_zscores", {}),
    )
    context_snapshot["analysis_context_pack"] = snapshot.model_dump()
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.trading_calendar import (
    get_market_for_stock,
    infer_market_phase,
    is_market_open,
)
from src.schemas.analysis_context_pack import (
    AnalysisSnapshot,
    ChipContext,
    FundamentalContext,
    MarketPhaseContext,
    SentimentContext,
    TechnicalContext,
)

logger = logging.getLogger(__name__)

# Human-readable phase labels (Chinese, matching project locale)
_PHASE_LABELS: dict[str, str] = {
    "premarket": "盘前",
    "intraday": "盘中",
    "lunch_break": "午休",
    "closing_auction": "集合竞价",
    "postmarket": "盘后",
    "non_trading": "非交易日",
    "unknown": "未知",
}

# Fields whose presence in news_content indicate it's non-empty
_NEWS_SENTIMENT_KEYWORDS: dict[str, float] = {
    "看多": 0.75,
    "利好": 0.8,
    "上涨": 0.65,
    "增长": 0.6,
    "突破": 0.7,
    "买入": 0.7,
    "推荐": 0.65,
    "跑赢": 0.7,
    "看空": 0.3,
    "利空": 0.25,
    "下跌": 0.35,
    "减持": 0.3,
    "卖出": 0.3,
    "风险": 0.35,
    "亏损": 0.2,
    "下调": 0.3,
}


class AnalysisContextBuilder:
    """Builds a validated AnalysisSnapshot from raw pipeline data."""

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def build_snapshot(
        self,
        enhanced_context: dict[str, Any] | None = None,
        news_content: str | None = None,
        realtime_quote: Any = None,
        chip_data: Any = None,
        factor_zscores: dict[str, float] | None = None,
        trading_calendar_utils: dict[str, Any] | None = None,
    ) -> AnalysisSnapshot:
        """
        Build a complete AnalysisSnapshot from pipeline data.

        Parameters
        ----------
        enhanced_context : dict | None
            The full enhanced context dict from ``pipeline._enhance_context()``.
            Expected keys: ``code``, ``stock_name``, ``today`` (with ma5/ma10/ma20),
            ``realtime`` (with pe_ratio/pb_ratio/volume_ratio/total_mv),
            ``ma_status``, ``fundamental_context``.
        news_content : str | None
            Concatenated news/intel report text.
        realtime_quote : UnifiedRealtimeQuote | None
            Raw realtime quote dataclass (used for market-phase detection).
        chip_data : ChipDistribution | None
            Raw chip distribution dataclass.
        factor_zscores : dict | None
            Cross-sectional factor z-scores, e.g. ``{"momentum": 0.85, "value": -0.32}``.
        trading_calendar_utils : dict | None
            Unused; kept for API compatibility with future calendar overrides.

        Returns
        -------
        AnalysisSnapshot
            Fully populated snapshot. Missing fields degrade to ``None`` / defaults.
        """
        enhanced_context = enhanced_context or {}
        factor_zscores = factor_zscores or {}

        stock_code = enhanced_context.get("code", "")
        stock_name = enhanced_context.get("stock_name", "")

        snapshot = AnalysisSnapshot(
            stock_code=stock_code,
            stock_name=stock_name,
            market_phase=self._build_market_phase(stock_code, realtime_quote),
            technical=self._build_technical(enhanced_context),
            fundamental=self._build_fundamental(enhanced_context),
            sentiment=self._build_sentiment(news_content),
            chip=self._build_chip(chip_data),
            enhanced_raw=enhanced_context,
        )
        return snapshot

    # ------------------------------------------------------------------
    # Sub-builders
    # ------------------------------------------------------------------

    def _build_market_phase(
        self,
        stock_code: str,
        realtime_quote: Any,
    ) -> MarketPhaseContext:
        """
        Determine the current market phase for the stock.

        Uses ``trading_calendar.infer_market_phase()`` when possible;
        ``is_open`` is derived from the phase value itself
        (intraday / closing_auction = open).
        """
        try:
            market = get_market_for_stock(stock_code) or ""
            phase = infer_market_phase(market)
            phase_str = phase.value if hasattr(phase, "value") else str(phase)

            return MarketPhaseContext(
                phase=phase_str,
                market=market,
                is_open=phase_str in ("intraday", "closing_auction"),
                session_label=_PHASE_LABELS.get(phase_str, phase_str),
            )
        except Exception as exc:
            logger.debug("_build_market_phase failed: %s", exc)
            return MarketPhaseContext()

    def _build_technical(self, enhanced: dict[str, Any]) -> TechnicalContext:
        """
        Extract technical indicators from enhanced_context.

        Reads from ``today`` sub-dict (which holds intraday-overridden
        MA values), ``realtime`` sub-dict (volume_ratio), and
        top-level ``ma_status``.
        """
        try:
            today = enhanced.get("today") or {}
            realtime = enhanced.get("realtime") or {}

            return TechnicalContext(
                ma5=today.get("ma5"),
                ma10=today.get("ma10"),
                ma20=today.get("ma20"),
                ma50=None,  # MA50 is not computed in the current pipeline
                volume_ratio=realtime.get("volume_ratio"),
                rsi=None,  # RSI is not surfaced in enhanced_context today
                macd=None,  # MACD is not surfaced in enhanced_context today
                ma_status=enhanced.get("ma_status"),
            )
        except Exception as exc:
            logger.debug("_build_technical failed: %s", exc)
            return TechnicalContext()

    def _build_fundamental(self, enhanced: dict[str, Any]) -> FundamentalContext:
        """
        Extract fundamental indicators from enhanced_context.

        Priority: ``realtime`` sub-dict (live PE/PB from quote) >
        ``fundamental_context.valuation`` (staged fundamental block).

        ROE is computed as ``pb / pe * 100`` when both are available.
        """
        try:
            # Safely extract sub-dicts, handling None / non-dict gracefully
            realtime = {}
            fc = {}
            valuation = {}

            _r = enhanced.get("realtime")
            if isinstance(_r, dict):
                realtime = _r

            _f = enhanced.get("fundamental_context")
            if isinstance(_f, dict):
                fc = _f
                _v = fc.get("valuation")
                if isinstance(_v, dict):
                    valuation = _v

            # Prefer realtime pe/pb, fall back to fundamental_context valuation
            pe = realtime.get("pe_ratio") or valuation.get("pe_ratio")
            pb = realtime.get("pb_ratio") or valuation.get("pb_ratio")
            total_mv = realtime.get("total_mv") or valuation.get("total_mv")

            # Compute ROE from pb/pe if both available
            roe = None
            if pe is not None and pb is not None and pe > 0:
                try:
                    roe = round(float(pb) / float(pe) * 100, 1)
                except (TypeError, ValueError, ZeroDivisionError):
                    pass

            return FundamentalContext(
                pe=float(pe) if pe is not None else None,
                pb=float(pb) if pb is not None else None,
                roe=roe,
                market_cap=float(total_mv) if total_mv is not None else None,
            )
        except Exception as exc:
            logger.debug("_build_fundamental failed: %s", exc)
            return FundamentalContext()

    def _build_sentiment(self, news_content: str | None) -> SentimentContext:
        """
        Derive sentiment from the raw news_content string.

        Heuristic: count positive/negative keyword occurrences to produce a
        coarse sentiment score. News count is estimated from line/section
        boundaries.

        Returns a neutral (0.5) default when no news is available.
        """
        try:
            if not news_content or not news_content.strip():
                return SentimentContext()

            # Estimate news count from markdown section headers or blank-line
            # separated paragraphs (conservative: at least 1).
            lines = [l for l in news_content.split("\n") if l.strip()]
            est_count = max(1, sum(1 for l in lines if l.startswith("###") or l.startswith("##")) or 1)

            # Extract top headlines (lines starting with ## / ### / - or numbered)
            headlines = []
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("##") or stripped.startswith("- ") or stripped[0].isdigit():
                    # Clean up markdown formatting for readability
                    clean = stripped.lstrip("#").lstrip("-").lstrip("0123456789.").strip()
                    if clean and len(clean) > 5:
                        headlines.append(clean[:60])
                        if len(headlines) >= 5:
                            break

            # Keyword-based sentiment scoring
            pos_score = 0.0
            neg_score = 0.0
            text_lower = news_content.lower()
            for keyword, score in _NEWS_SENTIMENT_KEYWORDS.items():
                if keyword.lower() in text_lower:
                    if score > 0.5:
                        pos_score += score
                    else:
                        neg_score += 1.0 - score

            if pos_score > 0 or neg_score > 0:
                net = (pos_score - neg_score) / (pos_score + neg_score)
                # Map net [-1, 1] → sentiment [0, 1]
                sentiment = round(0.5 + net * 0.35, 2)
                sentiment = max(0.0, min(1.0, sentiment))
            else:
                sentiment = 0.5

            return SentimentContext(
                news_sentiment=sentiment,
                news_count=est_count,
                top_news=headlines,
            )
        except Exception as exc:
            logger.debug("_build_sentiment failed: %s", exc)
            return SentimentContext()

    def _build_chip(self, chip_data: Any) -> ChipContext:
        """
        Extract chip-distribution context from raw chip_data.

        Expects chip_data to be a ``ChipDistribution`` dataclass (or any object
        with ``concentration_90``, ``avg_cost``, ``profit_ratio`` attributes).

        Falls back to reading from a dict if chip_data is already serialised.
        """
        try:
            if chip_data is None:
                return ChipContext()

            # Dataclass / object access
            if hasattr(chip_data, "concentration_90"):
                c90 = float(chip_data.concentration_90) if chip_data.concentration_90 is not None else None
                c70 = float(chip_data.concentration_70) if chip_data.concentration_70 is not None else None
                avg_cost = float(chip_data.avg_cost) if chip_data.avg_cost is not None else None
            elif isinstance(chip_data, dict):
                c90 = chip_data.get("concentration_90")
                c70 = chip_data.get("concentration_70")
                avg_cost = chip_data.get("avg_cost")
            else:
                return ChipContext()

            # Determine concentration description
            concentration_desc: str | None = None
            if c90 is not None:
                if c90 < 0.08:
                    concentration_desc = "高度集中"
                elif c90 < 0.15:
                    concentration_desc = "较集中"
                elif c90 < 0.25:
                    concentration_desc = "中等"
                else:
                    concentration_desc = "较分散"

            cost_dist: list[float] | None = None
            if c90 is not None or c70 is not None:
                cost_dist = [c90 or 0.0, c70 or 0.0]

            return ChipContext(
                concentration=concentration_desc,
                cost_distribution=cost_dist,
                chip_avg_cost=avg_cost,
            )
        except Exception as exc:
            logger.debug("_build_chip failed: %s", exc)
            return ChipContext()
