# ml-factor-loader Specification

## Purpose
MLFactorLoader 从 MindLynx 的 ML 因子系统加载因子层信号。读取 `data/realtime/ml_signal.json` 文件，提取每只股票的 `l7_score`、`composite_score`、`composite_label` 和 top 因子值，供融合引擎的 alpha158 增强使用。

## Requirements
### Requirement: MLFactorLoader loads ML factor signals

The system SHALL provide an `MLFactorLoader` class that loads ML factor signals from `ml_signal.json`.

#### Scenario: Load ML factors by date
- **WHEN** `MLFactorLoader().load_by_date("2026-07-01")` is called and `ml_signal.json` exists
- **THEN** it returns a dict of `{stock_code: {ml_factor_l7, ml_factor_score, ml_factor_label}}`

#### Scenario: Signal file missing
- **WHEN** `ml_signal.json` does not exist
- **THEN** it returns an empty dict

#### Scenario: JSON parse error
- **WHEN** `ml_signal.json` is malformed
- **THEN** it returns an empty dict and logs a debug message
