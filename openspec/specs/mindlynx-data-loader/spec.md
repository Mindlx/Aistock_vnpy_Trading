# mindlynx-data-loader Specification

## Purpose
MindLynxDataLoader 从 MindLynx-Aistock 子系统加载 LLM 分析报告。优先查询 `stock_analysis.db` 的 `analysis_history` 表，若数据库无记录则降级解析 Markdown 报告文件。输出包含操作建议、评分、趋势预测等核心字段。

## Requirements
### Requirement: MindLynxDataLoader loads analysis reports

The system SHALL provide a `MindLynxDataLoader` class that loads MindLynx analysis reports for a given date.

#### Scenario: Load from DB (primary path)
- **WHEN** `MindLynxDataLoader().load_by_date("2026-07-01")` is called and `stock_analysis.db` has records for that date
- **THEN** it returns parsed report data from `analysis_history` table with fields: `signal`, `score`, `trend`, `sentiment_score`, `operation_advice`, `analysis_summary`

#### Scenario: DB empty falls back to Markdown
- **WHEN** `stock_analysis.db` has no records for the given date
- **THEN** it falls back to parsing Markdown report files from the reports directory

#### Scenario: Both sources exhausted
- **WHEN** neither DB nor Markdown files contain data
- **THEN** it returns an empty dict
