# AT 子系统优化评估 — Oracle + c1skill 联合审查

> 审计日期: 2026-06-12 | 审查范围: mind_TradingAgent 子系统 + 融合系统
> 触发: AT 回测准确率 54.2%（26/48），融合三系统中最弱

---

## 执行摘要

对 mind_TradingAgent（at）子系统进行全面审查，发现核心问题：**数据源严重不可用 + 缺乏客观信号锚定**。Akshare 的 RemoteDisconnected 失败率接近 100%，efinance 也经常不可用，10只股票中 4-5只被跳过。同时 AT 缺少 LY+ML 的客观量化信号作为 LLM 辩论的参考基准。

建议方案 B（LY+ML 客观信号注入 AT + 改为始终注入 + 保留 Agent 自有工具调用）作为后续优化方向。

---

## 一、现状：数据源严重降级

### 1.1 降级链

```
AT 数据源验证 (verify_stock):
  akshare → (失败) → efinance → (失败) → yfinance (唯一可用)
  
  fast_degrade = akshare失败 AND efinance失败 → True
```

### 1.2 实际日志证据 (ta-cron.log)

```
akshare get_stock_data(601801.SS) failed:
  ('Connection aborted.', RemoteDisconnected(...))

akshare get_indicators(300676.SZ) failed:
  ('Connection aborted.', RemoteDisconnected(...))

akshare get_indicators(600372.SS) failed: (重复20+次)

fallback news: module 'akshare' has no attribute 'stock_info_news'
  ↑ fallback函数在已安装版本中已移除 → 永远失败

A股数据 [300652]: 所有数据源不可用，跳过分析
A股数据 [605368]: 所有数据源不可用，跳过分析
A股数据 [000592]: 所有数据源不可用，跳过分析
A股数据 [603189]: 所有数据源不可用，跳过分析
```

### 1.3 现有降级注入路径

`mind_agent_wrapper.py` 已有 `_get_preloaded_context()` 函数，从 UnifiedCache + ML DB 提取 OHLCV/技术指标/基本面/新闻——但**仅当 `fast_degrade=True` 时触发**，且**缺少 LY 双模型信号**。

参考: `src/mind_agent_wrapper.py:154-206`，`src/ashare_data.py:122-125`

---

## 二、已完成的 AT A 股化改造 (5月30日)

| 改造项目 | 提交 | 具体内容 |
|----------|------|----------|
| 10个Agent prompts全改造 | 38a8ed6 | 新闻:政策优先框架；基本面:7点红旗清单；多空:北向/主力/融资盘维；组合经理:T+1流动性/涨跌停 |
| 新闻源切到东方财富 | af21f02 | get_news() 以 stock_news_em() 为主 |
| 数据源全面增强 | 511bda9 | P0:雪球+东方财富股吧；P1:北向+主力流向；P2:央行/LPR/CPI/PMI宏观查询；P3:完整技术指标 |
| akshare优先，yfinance降级 | 587c5cd | akshare→yfinance 降级链 |
| 情报系统升级 | 3fd4a23 | 巨潮公告+CCTV新闻+全市场风险提示 |

---

## 三、回测表现

| 系统 | 准确率 | 评估次数 |
|------|--------|---------|
| 融合 | 62.3% | 69 |
| Lynx (ly) | 62.8% | 43 |
| MindLynx (ml) | 77.8% | 27 |
| **TradingAgent (at)** | **54.2%** | **48** |

数据范围: 2026-05-30 ~ 2026-06-12，11天，10只股票

---

## 四、Oracle 架构分析

### 4.1 根本问题

AT 是纯 LLM 角色扮演辩论，缺少客观信号锚定。当 10 个 Agent 辩论时，缺乏独立的量化基准来判断方向。叠加数据源大面积不可用，Agent 的信息输入严重受限。

### 4.2 LY→ML 注入模式已验证

commit `a1187c0` (2026-06-12) 已实现 LY 双模型信号注入 ML 子系统的 LLM 上下文:
- `_load_ly_signals()` 读取 ly_signal.json + ly_alpha_signal.json + prob_up_log.csv
- 输出结构化 Markdown 表格含: 综合上涨概率、RF/LGB各自概率、L7得分、模型分歧度、置信度标签
- 通过 executor.py 的 `_build_user_message` 和 analyzer.py 的 `_format_prompt` 注入

### 4.3 AT 当前注入通道缺失

`mind_agent_wrapper.py` 的 `_get_preloaded_context()` 已有数据提取框架（UnifiedCache + ML DB），但:
- LY 双模型信号未包含
- 仅在 `fast_degrade=True` 时触发（降级回退路径，非始终注入）

---

## 五、改进方案评估

| 方案 | 操作 | 工作量 | 预期 | 风险 |
|------|------|--------|------|------|
| A: LY信号注入AT | _get_preloaded_context() 增加 ly_signal.json 读取 | ~90行 | AT 获得LY量化基准 | 低 |
| B: LY+ML全量注入 + 始终注入 | 方案A + 注入ML分析 + 改为始终注入非仅降级 | ~150行 | 全谱客观锚定 | 中（数据冲突风险） |
| C: 方案B + 评分极端化修复 | 方案B + normalizer增加AT中间档位 | ~200行 | 全面改善 | 高 |
| D: 先降权到0.10观察 | 改weights.yaml | ~5行 | 减少噪声 | 安全但不解决 |

### 5.1 推荐的方案 B 细节

1. 将 `should_inject = data_check.get("fast_degrade", False)` 改为始终 `True`
2. 在 `_get_preloaded_context()` 中增加读取 `ly_signal.json` + `ly_alpha_signal.json` 的 LY 双模型信号
3. 保留 Agent 调用自有工具的能力（注入是参考而非替代）
4. 修改回填 `data/realtime/at_signal.json` 的内容格式

---

## 六、引用文件

- `src/mind_agent_wrapper.py` — AT 封装器 + 注入逻辑
- `src/ashare_data.py` — A股数据降级链
- `src/normalizer.py:287-308` — AT 5级评级→L7 映射
- `systems/mind_TradingAgent/mind_tradingagent/graph/trading_graph.py` — AT 辩论管线
- `systems/mind_TradingAgent/mind_tradingagent/graph/signal_processing.py` — 信号提取
- `systems/mind_TradingAgent/mind_tradingagent/agents/utils/rating.py` — 5级评级
- `docs/research/weight-c1skill-review.md` — 之前权重和AT相关论证
- `docs/research/full-system-audit-c1skill-oracle.md` — 全系统审计
- `docs/research/archive/fusion_architecture_research.md` — Oracle 方法论源头

## 七、状态

🟡 已分析完成，待深度研究后实施
