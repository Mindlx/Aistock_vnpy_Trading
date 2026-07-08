# AT 离线事故分析 & 修复记录

> 分析日期: 2026-07-08
> 修复日期: 2026-07-08
> 状态: 已修复 ✅

---

## 一、事故概要

| 维度 | 内容 |
|------|------|
| **症状** | 自 2026-06-26 起，TradingAgent 持续输出空信号，`at_signal.json` 中 `stocks: {}`，融合日报 18 只股票全部 `ta_is_stale=true` |
| **影响范围** | AT 作为分歧第三方投票完全缺失，分歧场景（~22%）仅有 LY vs ML 两方互搏 |
| **修复代价** | 2 处代码修改，总计 +8 行 / -2 行 |

## 二、根因分析 — 双层故障叠加

### 第一层：数据源三路全断（2026-06-26）

```python
verify_stock() 验证每只股票时的降级链：
  akshare   → RemoteDisconnected（服务端拒绝连接）
  efinance  → ModuleNotFoundError（未安装）
  yfinance  → 当日临时不可用

结果: available=False → 跳过所有股票 → 0/12 只分析完成
```

| 数据源 | 安装状态 | API 状态 | 说明 |
|:------:|:--------:|:--------:|------|
| akshare | ✅ v1.18.64 | ❌ RemoteDisconnected | 东方财富 API 服务端持续拒绝连接（非代码问题） |
| efinance | ❌ 未安装 | N/A | 降级链第二环缺失 |
| yfinance | ✅ v1.4.1 | ✅ 当前可用 | 6/26 当日临时故障 |

**降级链设计本意**：`akshare → efinance → yfinance` 三级兜底，任一源可用即可。但当日三层同时失效，属于罕见边缘事件。

### 第二层：改造引入代码 bug（2026-06-29）

```python
# systems/mind_TradingAgent/mind_tradingagent/graph/trading_graph.py

def _create_tool_nodes(self) -> dict[str, ToolNode]:
    return {
        "market": ToolNode([...]),         # ✅
        "social": ToolNode([...]),         # ✅
        "news": ToolNode([...]),           # ✅
        "fundamentals": ToolNode([...]),   # ✅
        # ❌ "policy" 和 "capital_flow" 完全缺失
    }
```

```python
# src/mind_agent_wrapper.py — _ensure_imported()

self._ta = TradingAgentsGraph(
    selected_analysts=["market", "social", "news", "fundamentals",
                       "policy", "capital_flow"],  # ← 6个分析师
)
```

用户于 `fb72ad1` 提交（2026-06-29）在 `selected_analysts` 中增加了 `"policy"` 和 `"capital_flow"`，但 `_create_tool_nodes()` 未同步更新。导致：

```
setup_graph() → analyst_execution_plan 生成 6 个 spec
  → workflow.add_node(spec.tool_node, self.tool_nodes[spec.key])
  → self.tool_nodes["policy"] → KeyError ← 崩溃
```

该异常被 `run_daily.py` 的 `except Exception` 静默吞没，TA 返回空结果。

### 双层叠加效应

| 时间 | 事件 | 说明 |
|:----:|:----:|------|
| 6/26 13:02 | 数据源三路全断 | TA 返回 0/12，`at_signal.json` 写入空 stocks |
| 6/27-6/28 | 周末 | TA 定时器不触发 |
| 6/28 | `07b45f2` 提交 | 静默吞异常加日志（修复尝试，未触及根因） |
| 6/29 | `fb72ad1` 提交 | 新增 policy/capital_flow，但引入 KeyError（新 bug） |
| 6/29 | `a2f7be1` 提交 | 添加 policy_analyst.py / capital_flow_tracker.py |
| 6/29 起 | KeyError 持续 | TA 无法初始化，0/18 只分析完成 |
| 7/8 | **本次修复** | 补全 tool_nodes + 修复参数兼容 |

6/26 的数据源问题和 6/29 的代码 bug 在时间上巧合地连续发生，导致 AT 一直未能自动恢复。

---

## 三、修复内容

### 修复 1：补全 tool_nodes

**文件**: `systems/mind_TradingAgent/mind_tradingagent/graph/trading_graph.py`

- 导入 `get_capital_flows`（来自 `agent_utils`）
- 添加 `"policy": ToolNode([get_news, get_global_news, get_fundamentals])`
- 添加 `"capital_flow": ToolNode([get_capital_flows])`

这两个分析师实际使用的工具函数均已存在于 `agent_utils.py` 中，无需额外实现。

### 修复 2：参数兼容

**文件**: `src/mind_agent_wrapper.py`

```python
# 修改前
def _injected_create(company_name, trade_date, asset_type="stock", past_context=""):
    state = orig_create(company_name, trade_date, asset_type, past_context)

# 修改后
def _injected_create(company_name, trade_date, asset_type="stock", past_context="", instrument_context=""):
    state = orig_create(company_name, trade_date, asset_type, past_context, instrument_context)
```

AT 的 `create_initial_state` 在 `propagation.py` 中新增了 `instrument_context` 参数，但 wrapper 的 `_injected_create` 未同步更新这个形参，导致参数不匹配崩溃。

### 修复验证

```python
✅ AT初始化成功
✅ tool_nodes中有policy: True
✅ tool_nodes中有capital_flow: True
✅ tool_nodes共6个: ['market', 'social', 'news', 'fundamentals', 'policy', 'capital_flow']
```

---

## 四、当前 AT 分析师架构

### 6 分析师职责

| key | 角色名 | 工具函数 | 功能 |
|:---:|:------:|:---------|------|
| market | Market Analyst | `get_stock_data`, `get_indicators`, `get_verified_market_snapshot` | 行情 + 技术指标 |
| social | Sentiment Analyst | `get_news` | 社交情绪/舆情 |
| news | News Analyst | `get_news`, `get_global_news`, `get_insider_transactions`, `get_macro_indicators`, `get_prediction_markets` | 新闻 + 宏观 |
| fundamentals | Fundamentals Analyst | `get_fundamentals`, `get_balance_sheet`, `get_cashflow`, `get_income_statement` | 基本面 + 财报 |
| policy | Policy Analyst | `get_news`, `get_global_news`, `get_fundamentals` | 政策监管（A 股适配） |
| capital_flow | Capital Flow Tracker | `get_capital_flows` | 主力资金流向（A 股适配） |

### 数据注入

AT 在分析前会通过 `_get_preloaded_context()` 从 LY 缓存和 ML 数据库注入信号：
- LY 量化信号（技术面判断）
- ML 因子信号（12 因子 + LLM 推理）
- 行情与技术指标
- 基本面数据
- 情绪数据
- 新闻数据

数据仓库（WarehouseReader）在分析前进行预热，但预热失败不会阻断分析流程。

---

## 五、遗留问题

### 数据源降级链

| 问题 | 优先级 | 现状 |
|------|:------:|------|
| akshare API 持续断连 | 低 | yfinance 可兜底，无需紧急处理 |
| efinance 未安装 | 低 | 建议 `pip install efinance` 恢复降级链第二环 |
| yfinance 单点故障风险 | 中 | 仅 yfinance 一个源可用，一旦中断 AT 再次瘫痪 |

### AT 权重

当前 `config/settings.yaml` 中 `tradingagent: 0.00`，AT 恢复运行后仅积累分歧数据，不参与融合加权。如需恢复 AT 投票权，需经长期数据验证后调整。

---

## 六、相关文件

| 文件 | 用途 |
|------|------|
| `src/mind_agent_wrapper.py` | TA 批量分析封装器 |
| `systems/mind_TradingAgent/mind_tradingagent/graph/trading_graph.py` | AT 图构建 + tool_nodes 定义 |
| `systems/mind_TradingAgent/mind_tradingagent/graph/setup.py` | 分析师编排 + 图设置 |
| `systems/mind_TradingAgent/mind_tradingagent/graph/analyst_execution.py` | 分析师规格定义 |
| `src/ashare_data.py` | A 股数据降级链（akshare → efinance → yfinance） |
| `config/logs/ta-cron.log` | TA + 融合运行日志 |
| `data/realtime/at_signal.json` | AT 准实时信号文件 |
| `scripts/run_daily.py`（行 309-419） | TA 运行入口 + write_at_signal |
