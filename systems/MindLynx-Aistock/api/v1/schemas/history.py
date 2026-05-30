"""
===================================
历史记录相关模型
===================================

职责：
1. 定义历史记录列表和详情模型
2. 定义分析报告完整模型
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HistoryItem(BaseModel):
    """历史记录摘要（列表展示用）"""

    id: int | None = Field(None, description="分析历史记录主键 ID")
    query_id: str = Field(..., description="分析记录关联 query_id（批量分析时重复）")
    stock_code: str = Field(..., description="股票代码")
    stock_name: str | None = Field(None, description="股票名称")
    report_type: str | None = Field(None, description="报告类型")
    sentiment_score: int | None = Field(
        None,
        description="情绪评分（历史数据可能超出 0-100 范围，读取时不做约束）",
    )
    operation_advice: str | None = Field(None, description="操作建议")
    created_at: str | None = Field(None, description="创建时间")

    class Config:
        json_schema_extra = {
            "example": {
                "id": 1234,
                "query_id": "abc123",
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "report_type": "detailed",
                "sentiment_score": 75,
                "operation_advice": "持有",
                "created_at": "2024-01-01T12:00:00",
            }
        }


class HistoryListResponse(BaseModel):
    """历史记录列表响应"""

    total: int = Field(..., description="总记录数")
    page: int = Field(..., description="当前页码")
    limit: int = Field(..., description="每页数量")
    items: list[HistoryItem] = Field(default_factory=list, description="记录列表")

    class Config:
        json_schema_extra = {"example": {"total": 100, "page": 1, "limit": 20, "items": []}}


class DeleteHistoryRequest(BaseModel):
    """删除历史记录请求"""

    record_ids: list[int] = Field(default_factory=list, description="要删除的历史记录主键 ID 列表")


class DeleteHistoryResponse(BaseModel):
    """删除历史记录响应"""

    deleted: int = Field(..., description="实际删除的历史记录数量")


class NewsIntelItem(BaseModel):
    """新闻情报条目"""

    title: str = Field(..., description="新闻标题")
    snippet: str = Field("", description="新闻摘要（最多200字）")
    url: str = Field(..., description="新闻链接")

    class Config:
        json_schema_extra = {
            "example": {
                "title": "公司发布业绩快报，营收同比增长 20%",
                "snippet": "公司公告显示，季度营收同比增长 20%...",
                "url": "https://example.com/news/123",
            }
        }


class NewsIntelResponse(BaseModel):
    """新闻情报响应"""

    total: int = Field(..., description="新闻条数")
    items: list[NewsIntelItem] = Field(default_factory=list, description="新闻列表")

    class Config:
        json_schema_extra = {"example": {"total": 2, "items": []}}


class ReportMeta(BaseModel):
    """报告元信息"""

    model_config = ConfigDict(protected_namespaces=("model_validate", "model_dump"))

    id: int | None = Field(None, description="分析历史记录主键 ID（仅历史报告有此字段）")
    query_id: str = Field(..., description="分析记录关联 query_id（批量分析时重复）")
    stock_code: str = Field(..., description="股票代码")
    stock_name: str | None = Field(None, description="股票名称")
    report_type: str | None = Field(None, description="报告类型")
    report_language: str | None = Field(None, description="报告输出语言（zh/en）")
    created_at: str | None = Field(None, description="创建时间")
    current_price: float | None = Field(None, description="分析时股价")
    change_pct: float | None = Field(None, description="分析时涨跌幅(%)")
    model_used: str | None = Field(None, description="分析使用的 LLM 模型")


class ReportSummary(BaseModel):
    """报告概览区"""

    analysis_summary: str | None = Field(None, description="关键结论")
    operation_advice: str | None = Field(None, description="操作建议")
    trend_prediction: str | None = Field(None, description="趋势预测")
    sentiment_score: int | None = Field(
        None,
        description="情绪评分（历史数据可能超出 0-100 范围，读取时不做约束）",
    )
    sentiment_label: str | None = Field(None, description="情绪标签")


class ReportStrategy(BaseModel):
    """策略点位区"""

    ideal_buy: str | None = Field(None, description="理想买入价")
    secondary_buy: str | None = Field(None, description="第二买入价")
    stop_loss: str | None = Field(None, description="止损价")
    take_profit: str | None = Field(None, description="止盈价")


class ReportDetails(BaseModel):
    """报告详情区"""

    news_content: str | None = Field(None, description="新闻摘要")
    raw_result: Any | None = Field(None, description="原始分析结果（JSON）")
    context_snapshot: Any | None = Field(None, description="分析时上下文快照（JSON）")
    financial_report: Any | None = Field(None, description="结构化财报摘要（来自 fundamental_context）")
    dividend_metrics: Any | None = Field(None, description="结构化分红指标（含 TTM 口径）")
    belong_boards: Any | None = Field(None, description="关联板块列表")
    sector_rankings: Any | None = Field(None, description="板块涨跌榜（结构 {top, bottom}）")


class AnalysisReport(BaseModel):
    """完整分析报告"""

    meta: ReportMeta = Field(..., description="元信息")
    summary: ReportSummary = Field(..., description="概览区")
    strategy: ReportStrategy | None = Field(None, description="策略点位区")
    details: ReportDetails | None = Field(None, description="详情区")

    class Config:
        json_schema_extra = {
            "example": {
                "meta": {
                    "query_id": "abc123",
                    "stock_code": "600519",
                    "stock_name": "贵州茅台",
                    "report_type": "detailed",
                    "report_language": "zh",
                    "created_at": "2024-01-01T12:00:00",
                },
                "summary": {
                    "analysis_summary": "技术面向好，建议持有",
                    "operation_advice": "持有",
                    "trend_prediction": "看多",
                    "sentiment_score": 75,
                    "sentiment_label": "乐观",
                },
                "strategy": {
                    "ideal_buy": "1800.00",
                    "secondary_buy": "1750.00",
                    "stop_loss": "1700.00",
                    "take_profit": "2000.00",
                },
                "details": None,
            }
        }


class MarkdownReportResponse(BaseModel):
    """Markdown 格式报告响应"""

    content: str = Field(..., description="Markdown 格式的完整报告内容")

    class Config:
        json_schema_extra = {
            "example": {"content": "# 📊 贵州茅台 (600519) 分析报告\n\n> 分析日期：**2024-01-01**\n\n..."}
        }
