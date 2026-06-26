# 校准参数恢复 & 代码审查修复 — 2026-06-26

## 背景

上游同步(6f6178e)将 MindLynx-Aistock 子系统替换为上游 v3.21 版本，覆盖了前期积累的 31 处校准优化参数，导致 ML 准确率从 ~61% 降至 55.9%。

## 修复范围

### 校准资产恢复（20 个文件，31 处）

| 文件 | 恢复内容 |
|------|---------|
| `prompt_shared.py` | 52/49 评分阈值 + 不对称性校准注释 + ACTION_GUARDRAILS 对齐 |
| `factor_engine.py` | MAD去极值(winsorize_mad) + NaN/零值保护 |
| `backtest_engine.py` | sentiment_score 回测全路径(Protocol/EvaluationConfig/evaluate_single/compute_summary) |
| `backtest_service.py` | sentiment_score 参数传递到 evaluate_single |
| `backtest_report.py` | sentiment_direction_accuracy_pct 双指标展示 |
| `backtest_repo.py` | upsert_summary 字段恢复 |
| `storage.py` | BacktestResult/BacktestSummary sentiment 列定义 |
| `analyzer.py` | LY量化信号注入 + prompt_anonymize脱敏 |
| `executor.py` | LY量化信号注入 |
| `stock_knowledge.py` | 历史分析回溯 + score_trend |
| `search_service.py` | 权威媒体排序(AUTHORITY_MEDIA) |
| `event_monitor.py` | 数据仓库写入(零侵入) |
| `market_analyzer.py` | "严禁编造价格数据" prompt守卫 |
| `market_review.py` | 缓存板块排名(get_cached_sector_rankings) |
| `main.py` | 市场情报搜集(新闻联播) + 周末跳过 + _push_highlights |
| 14个策略YAML | 理论家引用恢复(3个核心策略全量恢复) |

### 新增防护

| 文件 | 内容 |
|------|------|
| `scripts/backtest.py` | 多板块数据质量守卫(科创±20%/创业±20%/北交所±30%/主板±10%) |
| `scripts/backtest.py` | stock_code 空值/格式验证 |

### 代码审查修复（6 项）

| 问题 | 修复 |
|------|------|
| `sentiment_direction_correct` 用错方向(operation_advice→sentiment_direction) | `backtest_engine.py:271` |
| `prompt_anonymize` 脱敏被 `_format_prompt` 绕过(改变异context字典) | `analyzer.py:2441-2445` |
| BacktestResult 缺列(sentiment_score + sentiment_direction_correct) | `storage.py` |
| BacktestSummary 缺列(sentiment_direction_accuracy_pct) | `storage.py` |
| _build_summary_model / _summary_to_dict / _result_to_dict 缺字段 | `backtest_service.py` |
| upsert_summary attrs 列表缺字段 | `backtest_repo.py` |

### 数据库迁移

**stock_analysis.db — ALTER TABLE:**
- `backtest_results` ADD `sentiment_score` (INTEGER)
- `backtest_results` ADD `sentiment_direction_correct` (BOOLEAN)
- `backtest_summaries` ADD `sentiment_direction_accuracy_pct` (FLOAT) — 已存在，跳过

**analysis_history 数据导入:**
- 从上游目录 (`/workspace/MindLynx-Aistock/data/stock_analysis.db`) 导入 1093 条历史 ML 分析记录
- 覆盖日期范围：2026-05-18 ~ 2026-06-02
- 其中 1090 条来自 5 月（均有 sentiment_score）

**bt_results.db 重建:**
- 删除并重新创建 bt_predictions 表数据
- 来源：融合系统 CSV (`data/fusion_output/fusion_*.csv`, 46 个文件, 5/29~6/26)
- 回填 5 月 18-29 日 67 条 ML 历史记录（来自 analysis_history，使用原 60/40 阈值）

## 审计确认

- 104 个重叠文件全部交叉检查（同时存在于本地历史 + 同步提交）
- 融合层 (`src/`、`config/settings.yaml`) 未受影响
- 上游新增功能(relevance_score/JP-KR市场/ProviderTrace等)全部保留
- 6 项代码审查整改全部通过编译验证(13/13 文件)

## 最终指标

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| MindLynx ML 准确率 | 55.9% | **62.2%** (5月+6月, 273匹配) |
| 融合准确率 | — | 57.6% |
| 报告覆盖范围 | 22 天 | 30 天 (5/18~6/26) |
| 评估样本量 | 124 次 | 273 次 |

## 提交记录

- `dda0654` fix: 恢复上游同步覆盖的31处校准参数+6项代码审查修复（代码变更）
- 数据库文件(\*.db)在 `.gitignore` 中，不纳入 git 跟踪
