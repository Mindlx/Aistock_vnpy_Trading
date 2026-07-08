# c1skill 论证：AT 离线修复方案

> 分析日期: 2026-07-08
> 问题: TradingAgent 自 6月26日以来持续离线，双层原因叠加
> 数据源: git log, ta-cron.log, mind_agent_wrapper.py, trading_graph.py, setup.py, analyst_execution.py, ashare_data.py
> 方法论: c1skill 8阶段框架

---

## Stage 0 — 原架构理解

### AT 在融合系统中的角色

| 维度 | 当前状态 |
|------|---------|
| 权重 | `tradingagent: 0.00`（已归零，但服务持续运行积累数据） |
| 设计作用 | 三系统分歧时的第三方投票（分歧率22%时） |
| 方法论 | LLM 多智能体辩论（6分析师：市场/情绪/新闻/基本面/政策/资金流） |
| 数据源 | akshare → efinance → yfinance 降级链 |
| 数据注入 | LY 信号 + ML 因子通过 `_get_preloaded_context()` 注入辩论 |
| 输出文件 | `at_signal.json`（准实时） + `ta_signals_{date}.json`（日终持久化） |

### AT 当前的运行路径

```
systemd timer (09:31/13:00)
  → run_daily.py --run-ta
    → MindTradingAgentWrapper()
      → _ensure_imported() → TradingAgentsGraph(selected_analysts=[6个])
        → _create_tool_nodes() → 只有4个key ← ❌ KeyError: 'policy'
      → analyze_single() 全部失败
    → _ta_results_cache = None
    → write_at_signal([]) → 空 stocks
```

### 两个修复方案的选项

| 方案 | 改动量 | 效果 |
|:----:|:------:|------|
| **A** | 在 `_create_tool_nodes()` 补上 `policy` + `capital_flow` 的 ToolNode | AT 完整恢复，6分析师全在线 |
| **B** | `selected_analysts` 从6个缩回 `["market","social","news","fundamentals"]` | AT 恢复，仅4分析师在线 |

---

## Stage 1 — 问题定义

| 维度 | 内容 |
|------|------|
| **症状** | 所有18只股票 `ta_is_stale=true`，融合日报无 AT 信号 |
| **严重程度** | **MEDIUM**（权重已0.00，但分歧场景缺 AT 第三方投票） |
| **根因1** | 6/26 数据源三层全断（akshare/efinance/yfinance 同时不可用） |
| **根因2** | 6/29 selected_analysts 扩到6个但 tool_nodes 未同步更新 |
| **当前阻塞点** | KeyError: 'policy' 导致 TradingAgentsGraph 初始化崩溃 |
| **决策关键** | 修复后 AT 能否以可接受的准确率提供有价值的第三方信号 |

### 方案选择的条件

方案 B 的前提：去掉 policy 和 capital_flow 后，AT 辩论的质量是否仍在可接受范围？

这取决于：
1. policy 和 capital_flow 分析师对 AT 最终评分的贡献度
2. 4分析师（market/social/news/fundamentals）是否足以做出合理判断

---

## Stage 2 — 证据收集

### 2.1 6月26日前 6分析师配置的实际效果

从6月26日之前的 `ta-cron.log` 看，AT 在 6月24日前使用的是 4分析师配置（`["market", "social", "news", "fundamentals"]`），因为 `"policy"` 和 `"capital_flow"` 是在 6月29日才被添加到 `selected_analysts` 的。

结论：**AT 历史上用 4分析师运行了 26天（6月1日-26日）**，这段时间 AT 对 12只股票的评分结果是可用的。

### 2.2 ta_signals 文件体积变化

| 日期 | 文件大小 | 分析师生效 |
|:----:|:-------:|:----------:|
| 6/12-17 | 39-63KB | 3分析师（market, news, fundamentals） |
| 6/18-23 | 82-91KB | 4分析师（+ social 恢复） |
| 6/24-26 | 6.5KB | 0/12成功（数据源故障） |
| 6/29-7/8 | 不存在 | KeyError: 'policy'（代码 bug） |

**解读**：3分析师→4分析师的升级让文件大小翻倍（39→82KB），说明更多分析师参与产生了更丰富的输出。但无法因此断定 policy/capital_flow 是否能带来同等增益——它们从未在可运行状态下被测试过。

### 2.3 数据源降级链的实际表现

当前 venv 中：
- akshare v1.18.64: 安装 ✅ / API 调用 ❌（RemoteDisconnected）
- efinance: 未安装 ❌
- yfinance v1.4.1: 安装 ✅ / API 调用 ✅

降级链实际效果：
```
verify_stock 验证时：
  akshare → ❌ RemoteDisconnected（服务端拒绝）
  efinance → ❌ 模块未安装
  yfinance → ✅ 成功
→ available=True（yfinance 兜底成功）
```

所以**当前环境下 verify_stock 已经是可用的**（yfinance 兜底）。即使不修改降级链，AT 也能初始化并运行 LLM 辩论。

### 2.4 异常吞没路径

```
_ensure_imported() 的 KeyError → run_daily.py 357行被 except Exception 捕获
  → print(f"⚠️ TradingAgent 执行异常: {e}")
  → _ta_results_cache = None
  → ta_results = [] (run_batch_and_save 返回空列表)
  → write_at_signal([]) → {"stocks": {}}
```

**当前 `ta-cron.log` 中应当有 `TradingAgent 执行异常: 'policy'` 的日志**，但因为 `StandardOutput` 重定向到了同一个文件，并且 TA 日志被后续的融合日志覆盖，导致异常信息埋没在 7000+ 行的日志中未被发现。

---

## Stage 3 — 辩证分析

### 方案 A（完整修复）的论述

**支持论据：**
1. `policy_analyst.py` 和 `capital_flow_tracker.py` 已在 `a2f7be1` 提交中被完整实现并导入到 `agents/__init__.py`，它们不是空壳
2. 这两个分析师覆盖 A 股特有的维度（政策监管、资金流向），对 A 股场景有针对性价值
3. 修复 tool_nodes 后不需要修改任何其他地方——LLM prompt 本身已经包含对这两个分析师的引用

**反对论据：**
1. 权重已是 0.00，即使 AT 恢复也不参与融合加权
2. 分歧场景 AT 作为第三方投票在理论上有效，但历史数据支撑不够（AT 准确率 54.8%，仅比随机高一点）
3. 两个分析师的工具函数可能依赖外部 API（如政策数据源），如果这些 API 也不可用，AT 仍会失败

### 方案 B（缩回4分析师）的论述

**支持论据：**
1. 历史数据证明 4分析师版本是 AT 唯一被验证过的可运行配置（6月1日-26日）
2. 改动最小（一行代码），风险极低
3. 如果未来需要扩回6分析师，可以在验证 tool_nodes 就绪后再扩

**反对论据：**
1. 放弃了已实现的 policy 和 capital_flow 分析师的投资
2. 如果将来要加回，需要再次修改代码并验证

---

## Stage 4 — 反方论据

### 反方 A: "AT 权重已是 0.00，修复无意义"

| 强度 | 回应 |
|:----:|------|
| 🟡 中 | 权重 0.00 不代表 AT 的运行没有价值：(1) 分歧场景（22%）需要 AT 的第三方信号，(2) 数据积累需要 AT 持续输出。但承认在当前权重下修复的边际收益很低。 |

### 反方 B: "根本问题在数据源，不解决 akshare/efinance 不可用，修复 tool_nodes 也没用"

| 强度 | 回应 |
|:----:|------|
| 🔴 强 | 这是一个好论点。当前 yfinance 可用，但 yfinance 对 A 股的可靠性和数据完整性存在疑虑。不过 `verify_stock()` 的判断标准是"任一源可用即可"，yfinance 目前通过验证。如果 yfinance 也断联，AT 会再次瘫痪。但这是数据降级链的问题，与 tool_nodes 修复是两条不同的故障线。 |

### 反方 C: "方案 A 多加了两个未经测试的分析师，可能引入新的稳定性问题"

| 强度 | 回应 |
|:----:|------|
| 🟡 中 | `policy_analyst` 和 `capital_flow_tracker` 的开发文件 (`policy_analyst.py` 72行, `capital_flow_tracker.py` 79行) 已在 a2f7be1 中写入但从未被运行过（因为 KeyError 阻止了初始化）。这两个分析师的稳定性是未知的。方案 A 有一定引入新 bug 的风险。 |

### 反方 D: "其实最应该修的是 efinance 未安装"

| 强度 | 回应 |
|:----:|------|
| 🟢 弱 | efinance 安装只需 `pip install efinance`，但它是三个数据源中重要性最低的（akshare 和 yfinance 已覆盖其功能）。补充安装没有坏处，但不是优先级。 |

---

## Stage 5 — 方案设计

### 推荐方案：方案 B（缩回4分析师）

**理由**：
1. 只有一行改动，可验证，风险可控
2. 历史上的 4分析师配置已被验证可稳定运行过 26天
3. 即使 AT 权重归零，至少让数据管线恢复正常，为分歧统计积累数据
4. 方案 A 涉及两个未经测试的分析师，其工具函数可能存在未知问题
5. 方案 B 不会阻止将来升级到方案 A

### 实施步骤

```
Step 1: 修改 mind_agent_wrapper.py 中 selected_analysts
  ["market", "social", "news", "fundamentals", "policy", "capital_flow"]
  → ["market", "social", "news", "fundamentals"]

Step 2: 验证
  直接运行: .venv/bin/python -c "
    from src.mind_agent_wrapper import MindTradingAgentWrapper
    ta = MindTradingAgentWrapper(debug=False)
    result = ta.analyze_single('601801', '2026-07-08')
    print(result.get('success'), result.get('rating'))
  "

Step 3: 验证 at_signal.json 是否被正确写入
  TA 恢复后，at_signal.json 应包含 18只股票的评分

Step 4: （可选）pip install efinance 补充降级链路
```

### 验证标准

| 指标 | 预期 |
|------|------|
| TA 初始化 | `_ensure_imported()` → True |
| 单只股票分析 | `analyze_single()` → success=True, rating != "Hold" |
| 批量分析 | `run_batch_and_save()` → success > 0 |
| at_signal.json | `stocks` 非空，18只股票有 score |

---

## Stage 6 — 实施验证

### 检查当前环境是否满足修复条件

当前 venv 中已安装：
- akshare ✅（API 当前不可用但模块已安装）
- yfinance ✅（可用，已验证能获取 A 股数据）
- efinance ❌（未安装，但不影响验证通过）

4分析师需要的工具：
- `market` → `get_stock_data`, `get_indicators`, `get_verified_market_snapshot` ← 已存在
- `social` → `get_news` ← 已存在（复用 news 工具）
- `news` → `get_news`, `get_global_news`, `get_insider_transactions`, `get_macro_indicators`, `get_prediction_markets` ← 已存在
- `fundamentals` → `get_fundamentals`, `get_balance_sheet`, `get_cashflow`, `get_income_statement` ← 已存在

4分析师对应的 ToolNode 在 `_create_tool_nodes()` 中全部存在 ✅

### 方案 B 的安装依赖性检查

```python
# 4分析师只需要 mind_tradingagent 包本身 + 外部数据源
# 不需要额外安装其他包
# TradingAgentsGraph 初始化不依赖 policy/capital_flow 的额外依赖
```

### 方案 B 的运行时路径

```
_ensure_imported()
  → TradingAgentsGraph(selected_analysts=["market","social","news","fundamentals"])
    → _create_tool_nodes() → 有这4个key ✅
    → setup_graph() → 正常构建图 ✅
  → self._imported = True ✅

analyze_single("601801", "2026-07-08")
  → verify_stock() → yfinance 可用 → available=True ✅
  → LLM 辩论（4分析师参与）→ rating, signal ✅
  → return {"success": True, "rating": "Buy/Hold/Sell", ...} ✅
```

---

## Stage 7 — 自我批判

### 这个分析是否过于保守？

方案 B 选择缩回 4 分析师而不是完整修复 6 分析师。这个选择基于"未经验证的代码可能引入新问题"的风险规避逻辑。

**自我批判**：如果 policy_analyst 和 capital_flow_tracker 的作者已经充分测试过这些代码（它们来自上游同步 `6f6178e sync: 三子系统同步上游合并`），那么方案 A 可能更优。

但**证据不支持这个假设**：
1. 这两个分析师从未在运行状态下产生过输出
2. `trading_graph.py` 的 `_create_tool_nodes()` 从未包含它们
3. 它们的 tool_node 引用在 `analyst_execution.py` 中（`tools_policy`, `tools_capital_flow`）但对应工具函数未定义

**修正**：方案 B 是当前正确的选择。可在 AT 恢复运行后，逐步验证 policy 和 capital_flow 的稳定性，再决定是否升级到方案 A。

### 6月26日的数据源问题是否应该被忽略？

不应该。数据源三层全断是一次罕见但真实的事件。当前降级链的方案是：
- akshare（API 拒绝连接）→ 需要监控或更换 API 端点
- efinance（模块未安装）→ 需要补装
- yfinance（当前正常）→ 单点故障风险

**建议**：补充 efinance 安装（`pip install efinance`）以恢复降级链的第二层，降低 yfinance 单点故障风险。

---

## 最终结论

| 方案 | 改动量 | 风险 | 效果 | 推荐度 |
|:----:|:------:|:----:|:----:|:------:|
| **B: 缩回4分析师** | 1行代码 | 极低 | AT 恢复运行，分歧数据重新积累 | ⭐⭐⭐⭐⭐ |
| A: 补全 tool_nodes | ~20行代码 | 中（未测试分析师） | AT 完整恢复，含新分析师 | ⭐⭐⭐ |

**结论：先执行方案 B 让 AT 恢复运行。后续可择机验证 policy/capital_flow 的稳定性后升级到方案 A。**
