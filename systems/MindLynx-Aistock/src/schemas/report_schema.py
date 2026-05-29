"""
===================================
Report Engine - Pydantic Schema
===================================

Defines AnalysisReportSchema for validating LLM JSON output.
Aligns with SYSTEM_PROMPT in src/analyzer.py.
Uses Optional for lenient parsing; business-layer integrity checks are separate.
"""

from pydantic import BaseModel, ConfigDict, Field


class PositionAdvice(BaseModel):
    """Position advice for no-position vs has-position."""

    no_position: str | None = None
    has_position: str | None = None


class CoreConclusion(BaseModel):
    """Core conclusion block."""

    one_sentence: str | None = None
    signal_type: str | None = None
    time_sensitivity: str | None = None
    position_advice: PositionAdvice | None = None


class TrendStatus(BaseModel):
    """Trend status."""

    ma_alignment: str | None = None
    is_bullish: bool | None = None
    trend_score: int | float | str | None = None


class PricePosition(BaseModel):
    """Price position (may contain N/A strings)."""

    current_price: int | float | str | None = None
    ma5: int | float | str | None = None
    ma10: int | float | str | None = None
    ma20: int | float | str | None = None
    bias_ma5: int | float | str | None = None
    bias_status: str | None = None
    support_level: int | float | str | None = None
    resistance_level: int | float | str | None = None


class VolumeAnalysis(BaseModel):
    """Volume analysis."""

    volume_ratio: int | float | str | None = None
    volume_status: str | None = None
    turnover_rate: int | float | str | None = None
    volume_meaning: str | None = None


class ChipStructure(BaseModel):
    """Chip structure."""

    profit_ratio: int | float | str | None = None
    avg_cost: int | float | str | None = None
    concentration: int | float | str | None = None
    chip_health: str | None = None


class DataPerspective(BaseModel):
    """Data perspective block."""

    trend_status: TrendStatus | None = None
    price_position: PricePosition | None = None
    volume_analysis: VolumeAnalysis | None = None
    chip_structure: ChipStructure | None = None


class Intelligence(BaseModel):
    """Intelligence block."""

    latest_news: str | None = None
    risk_alerts: list[str] | None = None
    positive_catalysts: list[str] | None = None
    earnings_outlook: str | None = None
    sentiment_summary: str | None = None


class SniperPoints(BaseModel):
    """Sniper points (ideal_buy, stop_loss, etc.)."""

    ideal_buy: str | int | float | None = None
    secondary_buy: str | int | float | None = None
    stop_loss: str | int | float | None = None
    take_profit: str | int | float | None = None


class PositionStrategy(BaseModel):
    """Position strategy."""

    suggested_position: str | None = None
    entry_plan: str | None = None
    risk_control: str | None = None


class BattlePlan(BaseModel):
    """Battle plan block."""

    sniper_points: SniperPoints | None = None
    position_strategy: PositionStrategy | None = None
    action_checklist: list[str] | None = None


class Dashboard(BaseModel):
    """Dashboard block."""

    core_conclusion: CoreConclusion | None = None
    data_perspective: DataPerspective | None = None
    intelligence: Intelligence | None = None
    battle_plan: BattlePlan | None = None


class AnalysisReportSchema(BaseModel):
    """
    Top-level schema for LLM report JSON.
    Aligns with SYSTEM_PROMPT output format.
    """

    model_config = ConfigDict(extra="allow")  # Allow extra fields from LLM

    stock_name: str | None = None
    sentiment_score: int | None = Field(None, ge=0, le=100)
    trend_prediction: str | None = None
    operation_advice: str | None = None
    decision_type: str | None = None
    confidence_level: str | None = None

    dashboard: Dashboard | None = None

    analysis_summary: str | None = None
    key_points: str | None = None
    risk_warning: str | None = None
    buy_reason: str | None = None

    trend_analysis: str | None = None
    short_term_outlook: str | None = None
    medium_term_outlook: str | None = None
    technical_analysis: str | None = None
    ma_analysis: str | None = None
    volume_analysis: str | None = None
    pattern_analysis: str | None = None
    fundamental_analysis: str | None = None
    sector_position: str | None = None
    company_highlights: str | None = None
    news_summary: str | None = None
    market_sentiment: str | None = None
    hot_topics: str | None = None

    search_performed: bool | None = None
    data_sources: str | None = None
