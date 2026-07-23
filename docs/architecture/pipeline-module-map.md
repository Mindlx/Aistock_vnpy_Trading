# Pipeline 模块地图

> **文件**: `systems/MindLynx-Aistock/src/core/pipeline.py`  
> **大小**: 2927 行, 55 个方法/函数  
> **最后更新**: 2026-07-23  
> **目的**: 本文档帮助开发者快速定位 pipeline.py 中各功能的位置，无需通读全文件。

---

## 架构概览

```
用户请求 (analyzer.py/scheduler.py/bot/)
    ↓
StockAnalysisPipeline.run()
    ├─ process_single_stock() ← 逐个股票
    │   ├─ _gather_intelligence()   情报收集
    │   ├─ _analyze_traditional()   技术分析
    │   ├─ _enhance_context()       上下文增强
    │   ├─ _analyze_with_agent()    LLM 分析
    │   ├─ _post_process_agent()    后处理
    │   └─ agent_result...()        结果转换
    │
    ├─ _precompute_analysis_inputs() (批量模式)
    │   ├─ FactorEngine 因子计算
    │   ├─ Regime 市场状态
    │   └─ _store_factor_profiles()
    │
    └─ _compute_portfolio_allocation() (批量模式)
```

---

## 方法分组

### 1. 初始化与核心入口（行 61-182）

| 行号 | 方法 | 说明 | 调用方 |
|:----:|:-----|:-----|:-------|
| 61 | `_regime_atr_multiplier()` | 模块级辅助函数 | 内部 |
| 74 | `class StockAnalysisPipeline` | 主类（继承 DataMixin, NotificationMixin）| — |
| 84 | `__init__()` | 接收 config/analysis_skills/max_workers 等 | analyzer.py, scheduler.py, bot/ |
| 182 | `_emit_progress()` | 进度回调 | 内部 |
| 204 | `analyze_stock()` | **单股入口**：情报→技术→LLM→后处理 | 内部 run() |

### 2. 数据收集（行 455-1139）

| 行号 | 方法 | 说明 |
|:----:|:-----|:------|
| 455 | `_gather_intelligence()` | 多维度情报收集（实时行情+K线+新闻+资金流） |
| 487 | `_analyze_traditional()` | 传统技术分析（趋势分析器） |
| 544 | `_enhance_context()` | 合并行情/新闻/因子/情报→增强上下文 |
| 754 | `_compute_position_sizing()` | ATR 仓位计算 |
| 781 | `_enrich_risk_and_fundamental()` | 风险+基本面数据注入 |
| 832 | `_inject_intelligence_context()` | 情报上下文注入 |
| 898 | `_load_ly_signals()` | LY 系统信号加载（已注释） |
| 1106 | `_get_stock_eastmoney_rating()` | 东方财富评级 |
| 1139 | `_attach_belong_boards_to_fundamental_context()` | 板块归属 |

### 3. Agent 分析与 LLM 对话（行 1186-1674）

| 行号 | 方法 | 说明 |
|:----:|:-----|:------|
| 1186 | `_ensure_agent_history()` | 确保分析历史存在 |
| 1206 | `_analyze_with_agent()` | **LLM 对话入口**：构建 context → AgentExecutor → 循环 |
| 1254 | `_build_agent_context()` | 组装 Agent 上下文（因子/行情/新闻/板块等）|
| 1451 | `_build_agent_message()` | 构建最终消息体（含所有注入内容）|
| 1531 | `_process_agent_result()` | 处理 LLM 返回结果 |
| 1576 | `_post_process_agent()` | 后处理：存储 → 快照 → 信号入库 |
| 1674 | `_agent_result_to_analysis_result()` | Agent 结果 → AnalysisResult 转换 |

### 4. 结果处理与降级（行 1674-2286）

| 行号 | 方法 | 说明 |
|:----:|:-----|:------|
| 1855 | `_calibrate_operation_advice()` | 操作建议校准 |
| 1889 | `_agent_dashboard_value()` | 安全提取 dashboard 值 |
| 1917 | `_extract_advice_text_from_dict()` | 文本提取 |
| 1933 | `_is_agent_placeholder_text()` | 占位符检测 |
| 1944 | `_is_agent_field_missing()` | 字段缺失检测 |
| 1972 | `_trend_score_fallback()` | 趋势评分降级 |
| 1982 | `_trend_label_fallback()` | 趋势标签降级 |
| 1995 | `_trend_signal_fallback()` | 趋势信号降级 |
| 2006 | `_trend_decision_fallback()` | 趋势决策降级 |
| 2020 | `_mark_trend_fallback_source()` | 降级来源标记 |
| 2026 | `_summary_fallback_from_result()` | 摘要降级 |
| 2035 | `_backfill_agent_dashboard_fields()` | 回填 dashboard 字段 |
| 2150 | `_stop_loss_fallback_from_trend()` | 止损降级 |
| 2160 | `_safe_parse_number()` | 安全数值解析 |
| 2172 | `_ideal_buy_fallback_from_rating()` | 理想买入价降级 |
| 2223 | `_apply_trend_fallback()` | 综合趋势降级 |
| 2270 | `_is_placeholder_stock_name()` | 占位股名检测 |
| 2286 | `_safe_int()` | 安全整数转换 |

### 5. 辅助工具方法（行 2286-2425）

| 行号 | 方法 | 说明 |
|:----:|:-----|:------|
| 2302 | `_describe_volume_ratio()` | 量比文字描述 |
| 2322 | `_compute_ma_status()` | 均线状态计算 |
| 2342 | `_build_context_snapshot()` | 上下文快照构建 |
| 2383 | `_safe_to_dict()` | 安全转 dict |
| 2401 | `_resolve_query_source()` | 查询来源解析 |
| 2425 | `_build_query_context()` | 查询上下文构建 |

### 6. 批量处理入口（行 2450-2706）

| 行号 | 方法 | 说明 | 调用方 |
|:----:|:-----|:-----|:-------|
| 2450 | `process_single_stock()` | 单只股票全流程（含前计算） | `run()` |
| 2545 | `run()` | **批量入口**：调度股票 → 前计算 → 逐只分析 | analyzer.py, scheduler.py |

### 7. 因子计算管线（行 2711-2919）

| 行号 | 方法 | 说明 |
|:----:|:-----|:------|
| 2711 | `_precompute_analysis_inputs()` | 批量预计算因子/体制/仓位 |
| 2764 | `_apply_regime_weights()` | 应用体制权重 |
| 2781 | `_normalize_factors()` | 因子归一化 |
| 2790 | `_store_factor_profiles()` | 因子剖面存储 + 横截面排名 |
| 2829 | `_append_cross_sectional_rankings()` | 横截面排名追加 |
| 2846 | `_compute_uncertainty_and_ood()` | 不确定性与 OOD 检测 |
| 2885 | `_compute_portfolio_allocation()` | 投资组合分配 |
| 2907 | `_trigger_factor_recalibration_if_needed()` | 因子校准触发器（模块级） |

---

## 外部调用方

| 调用方 | 调用方法 | 用途 |
|:-------|:---------|:------|
| `analyzer.py` | `StockAnalysisPipeline(config).run()` | 定时批量分析 |
| `scheduler.py` | `StockAnalysisPipeline(config).run()` | 定时任务调度 |
| `bot/` 命令处理器 | `StockAnalysisPipeline(config).analyze_stock()` | 按需个股分析 |
| `market_review.py` | 部分 context 构建方法 | 复盘中用到 |

---

## 数据流

```
run(codes)
    ↓
_precompute_analysis_inputs(codes)
    ├─ FactorEngine.compute_for_stock() → raw_factors
    ├─ classify_regime() → 市场状态
    └─ classify_regime() → 持仓分配
    ↓
process_single_stock(code)  [循环每个股票]
    ├─ _gather_intelligence() → 行情+K线+新闻+资金流
    ├─ _analyze_traditional() → 技术指标
    ├─ _enhance_context() → 合并所有数据
    ├─ _analyze_with_agent() → LLM 对话
    │   ├─ _build_agent_context()
    │   ├─ AgentExecutor.chat()
    │   └─ _process_agent_result()
    ├─ _post_process_agent() → 存储
    │   └─ save_analysis_history(skill_id=...)
    └─ _apply_trend_fallback() (if needed)
```

---

## 大小分布

| 区域 | 行数 | 占比 | 说明 |
|:-----|:----:|:----:|:------|
| 入口 + 单股流程 | ~250 | 8% | `run()` + `process_single_stock()` |
| 数据收集 | ~700 | 24% | 情报/技术/上下文/仓位/风险 |
| Agent 对话 | ~500 | 17% | 构建 context/消息 + 结果处理 |
| **降级逻辑** | **~350** | **12%** | 8 种降级方法 |
| 结果转换 | ~250 | 8% | AnalysisResult 构建 |
| 因子管线 | ~200 | 7% | 因子计算/剖面/排名 |
| 辅助工具 | ~150 | 5% | safe_int, MA状态等 |
| 配置/初始化 | ~100 | 3% | __init__ |
| 其他(注释/空行/导入) | ~450 | 15% | — |

---

## 重构候选（如果未来要拆）

| 候选模块 | 涉及方法 | 行数 | 依赖 |
|:---------|:---------|:----:|:-----|
| `core/agent_prompt.py` | `_build_agent_message()`, `_build_agent_context()` | ~300 | 多个注入源 |
| `core/analysis_result.py` | `_agent_result_to_analysis_result()`, `_backfill_*()` | ~500 | storage.py |
| `core/trend_fallback.py` | `_trend_*_fallback()` 全部 | ~350 | 无 |
| `core/context_builder.py` | `_enhance_context()`, `_inject_*()` | ~400 | config |
