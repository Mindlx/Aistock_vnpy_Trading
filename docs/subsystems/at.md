# AT (TradingAgent) 子系统 — 多智能体辩论引擎

> 最后更新: 2026-07-21 (verify_stock 仓库优先 + max_debate_rounds修正)
> 子系统路径: `systems/mind_TradingAgent/`
> 封装器: `src/mind_agent_wrapper.py`

---

## 一、系统概述

TradingAgent (AT) 是三系统信号融合中的**多智能体辩论引擎**。6 位分析师各自分析股票，经过多头/空头投资辩论和风险辩论，由 Portfolio Manager 裁决输出 5 级评分。

### 数据流

```
股票代码
  → AshareDataProvider 验证数据源 (akshare → efinance → yfinance)
  → 数据注入: SignalLoader + ContextPreparer (LY信号 + ML因子 + 行情/基本面/新闻/情绪)
  → TradingAgentsGraph.propagate()
    → 6 分析师并行分析 (ToolNode)
    → 投资辩论 (Bull ↔ Bear) + 裁决
    → 风险辩论 (Aggressive ↔ Conservative ↔ Neutral) + 裁决
    → Portfolio Manager 综合 → 5级评分
  → 回落: StockAnalyzer.fallback_analysis() (纯技术指标)
```

### 输出

| 字段 | 类型 | 说明 |
|------|------|------|
| rating | str | Buy / Overweight / Hold / Underweight / Sell |
| score | float | L7 归一化 [-3, +3]: Buy=+3, Overweight=+2.06, Hold=0, Underweight=-1.13, Sell=-3 |
| debate_state | dict | 辩论一致性数据 (供贝叶斯融合使用) |

---

## 二、运行时架构

### 定时触发

- **systemd 服务**: `Aistock_vnpy_Trading-TA.service`
- **定时器: 10:10 / 13:00
- **执行命令**: `.venv/bin/python scripts/run_daily.py --run-ta`
- **执行结果**: 写入 `at_signal.json` (准实时文件交换区) + `data/tradingagent/ta_logs/` (全量日志)

### 6 位分析师

| key | 角色 | 工具函数 | 职责 |
|:---:|:----:|:---------|:-----|
| market | Market Analyst | get_stock_data, get_indicators, get_verified_market_snapshot | 行情 + 技术指标 |
| social | Sentiment Analyst | get_news | 社交舆情 |
| news | News Analyst | get_news, get_global_news, get_insider_transactions, get_macro_indicators, get_prediction_markets | 新闻 + 宏观 |
| fundamentals | Fundamentals Analyst | get_fundamentals, get_balance_sheet, get_cashflow, get_income_statement | 基本面 + 财报 |
| policy | Policy Analyst | get_news, get_global_news, get_fundamentals | 政策监管 |
| capital_flow | Capital Flow Tracker | get_capital_flows | 资金流向 |

### LLM 配置

```
provider: openai_compatible (SGLang 35B MoE, GPU1:15433)
deep_think_llm:  Qwen-3.6-35B-A3B-VRAP-4-bit-AWQ-21.2GB
quick_think_llm: Qwen-3.6-35B-A3B-VRAP-4-bit-AWQ-21.2GB
max_debate_rounds: 1 (default_config.py:105, 实际值, 文档曾误写为2)
max_risk_rounds: 1
```

---

## 三、数据注入机制

AT 在 LLM 辩论前会注入 LY 量化信号和 ML 因子数据，使分析师在 LLM 推理时获得三系统的综合信息。

### 注入路径

`src/mind_agent_wrapper.py` → `ContextPreparer.prepare_all()` → `build_injection_payload()`

注入内容:
- LY 量化信号 (RF+LGB 上涨概率、L7 得分、模型分歧)
- ML 因子信号 (14因子综合评分、前三因子)
- 行情与技术指标 (OHLCV、MA、RSI、MACD、布林带、ATR)
- 基本面 (板块、PE、PB、市值)
- 新闻与公告
- ML 分析摘要 (操作建议、评分、趋势预测)

### 注入方式

修改 `create_initial_state` 的 messages，将注入数据合并到首个 `("human", ...)` 消息中。
**不使用 SystemMessage 插入**，以兼容 SGLang 等 OpenAI 兼容 API 的消息排序要求。

---

## 四、离线事故记录

### 事故 #1: 数据源全断 + 代码 bug（2026-06-26 ~ 2026-07-08）

参见 `docs/subsystems/at-offline-incident.md`

**根因**: akshare 服务端断连 + efinance 未安装 + 改造引入 tool_nodes 缺失。

**修复**: 补全 policy/capital_flow 的 ToolNode + 参数兼容。

### 事故 #2: API Key 缺失 + None 格式化崩溃（2026-07-14 ~ 2026-07-17）

#### 症状

连续 7 轮融合 AT 参与度为 0，所有股票 `tradingagent_score=0.0, tradingagent_valid=false`。

#### 根因: 三层故障叠加

```
第一层: DEEPSEEK_API_KEY 未写入 .env
  └── 原配置 MIND_TRADINGAGENT_LLM_PROVIDER=deepseek
  └── ~/.secrets 只有 CLI/bashell 能读到，systemd 环境干净
  └── 影响: TA 初始化时 LLM 客户端创建失败 (ValueError)

第二层: _resolve_pending_entries None 格式化崩溃
  └── reflection.py:52: f"{alpha_return:+.1%}" ← alpha_return 为 None
  └── memory.py:195-196: 同上
  └── 连续几天 TA 失败遗留了 pending entries
  └── 修复后触发这些旧记录的结算 → 格式化 None 崩溃

第三层: SGLang 拒绝 SystemMessage 插入
  └── _injected_create() 插入 SystemMessage + AIMessage
  └── SGLang API 要求 system 消息必须在最前
  └── 影响: 所有股票的 LLM 辩论阶段失败
```

#### 修复内容

| # | 文件 | 修改 | 类型 |
|:-:|:----|:-----|:----:|
| 1 | `Aistock_vnpy_Trading/.env` | 追加 `DEEPSEEK_API_KEY` | 配置 |
| 2 | `systems/mind_TradingAgent/.env` | 追加 `DEEPSEEK_API_KEY` | 配置 |
| 3 | `systems/mind_TradingAgent/.env` | provider 改为 `openai_compatible`，指向 SGLang 35B | 配置 |
| 4 | `systems/mind_TradingAgent/mind_tradingagent/graph/reflection.py:52` | `f"{alpha_return:+.1%}"` → None 保护 | Bug 修复 |
| 5 | `systems/mind_TradingAgent/mind_tradingagent/agents/utils/memory.py:195-196` | 同上 | Bug 修复 |
| 6 | `systems/mind_TradingAgent/mind_tradingagent/graph/trading_graph.py:386` | `_resolve_pending_entries` 包 try/except | 防御 |
| 7 | `src/mind_agent_wrapper.py:188-192` | 数据注入改为 HumanMessage 合并，兼容 SGLang | Bug 修复 |

#### 从 DeepSeek 切换到本地 35B MoE

**原因**: TA 子系统一直配置纯 DeepSeek 远程调用，7月14日起因 API key 丢失持续离线。本地 SGLang 35B MoE (GPU1) 推理速度快、可用性高。

**对比分析**:

| 指标 | DeepSeek V4 Flash | 本地 35B MoE (SGLang/G1) |
|:----:|:-----------------:|:-------------------------:|
| 延迟 | ~3-8s/次 (网络) | ~1-3s/次 (本地) |
| 可用性 | 依赖 API key + 网络 | 始终可用 |
| 质量 | 强 (专用推理模型) | 强 (MoE 多专家多角度) |
| 成本 | 按 token 付费 | 0 |
| 切换代价 | 配置 .env | 配置 .env |

---

## 五、降级与恢复机制

### 数据源验证 (verify_stock)

```
验证链 (src/ashare_data.py):
  数据仓库 (本地SQLite, 微秒级) ← 2026-07-21 新增为首选
    → 成功 → available=True, 后续外部API仅做状态追踪
  akshare (RemoteDisconnected 持续)
  efinance
  yfinance (外部HTTP, 慢)
  
  之前: 全部外部API失败 → skip (错过仓库中已有数据)
  现在: 仓库有数据即通过, 外部API失败不再阻塞
```

### 降级链

```
LLM 多智能体辩论
  → 异常: Except Exception → StockAnalyzer.fallback_analysis()
  → fallback: yfinance + technical_rating() (MA/RSI/MACD/量比)
  → 返回 5 级评分
```

### 恢复时序

```
1. systemd timer 触发 (10:10/13:30)
2. MindTradingAgentWrapper._ensure_imported() → 加载 TA 子模块
3. 遍历 18 只股票:
   a. verify_stock() → 仓库优先(微秒) → 外部API(追踪)
   b. ContextPreparer.prepare_all() → 多源数据准备
   c. TradingAgentsGraph.propagate() → LLM 辩论
       ├── _resolve_pending_entries() → 历史结算 (try/except 保护)
       ├── 6 分析师 → 投资辩论 → 风险辩论 → PM 裁决
       └── 失败 → fallback_analysis()
4. 写入 at_signal.json + 融合引擎读取 → 推送
```

---

## 六、相关文件

| 文件 | 说明 |
|:----|:-----|
| `systems/mind_TradingAgent/` | TA 子模块根目录 |
| `systems/mind_TradingAgent/.env` | LLM provider 配置 (当前: 35B MoE) |
| `systems/mind_TradingAgent/mind_tradingagent/graph/trading_graph.py` | 主图构建 + tool_nodes |
| `systems/mind_TradingAgent/mind_tradingagent/graph/propagation.py` | 状态初始化与传播 |
| `systems/mind_TradingAgent/mind_tradingagent/graph/reflection.py` | 历史结果反思 |
| `systems/mind_TradingAgent/mind_tradingagent/graph/signal_processing.py` | 信号提取 |
| `systems/mind_TradingAgent/mind_tradingagent/agents/utils/memory.py` | 内存日志 (含 None 已修复) |
| `systems/mind_TradingAgent/mind_tradingagent/llm_clients/` | LLM 客户端 (factory + api_key_env) |
| `src/mind_agent_wrapper.py` | TA 批量分析封装器 (注入逻辑) |
| `src/services/context_preparer.py` | 数据注入准备 |
| `src/services/signal_loader.py` | LY/ML 信号加载 |
| `src/services/stock_analyzer.py` | 技术指标回落评分 |
| `src/ashare_data.py` | A 股数据降级链 |
| `scripts/run_daily.py` | TA + 融合入口 |
| `config/logs/ta-cron.log` | TA 运行日志 |
| `data/realtime/at_signal.json` | AT 准实时信号文件 |
| `data/tradingagent/ta_logs/` | AT 全量日志 (每股票 per date) |
| `config/systems.yaml` | 三系统权重配置 |
| `docs/subsystems/at-evolution-analysis.md` | 基线 vs 当前演化分析：bug溯源/功能增益/质量评估 |
| `~~docs/subsystems/at-debate-example-2026-06-23.md~~` | ~~601801 皖新传媒辩论完整记录（4角色全文） — 已归档清理~~ |
