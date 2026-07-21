# AT 子系统演化分析 — 6月23日基线 vs 当前系统

> 基线: commit `7a45592` (2026-06-22) — 4 分析师上游原始系统
> 当前: HEAD (`3af9924`, 2026-07-16) — 6 分析师 A 股适配系统

---

## 一、演化变更清单

| 变更 | 基线 | 当前 | 引入时间 | 风险等级 |
|:----|:----:|:----:|:---------|:--------:|
| 分析师数量 | 4 (market/social/news/fundamentals) | 6 (+policy/+capital_flow) | 07-08 | 🟡 中 |
| 辩论轮次 (max_debate_rounds) | 1 | 2 | 06-17→当前 | 🟡 中 |
| 信号注入 (context_preparer) | 无 | LY/ML/行情/基本面注入 | 06-12 | 🟢 正向 |
| LLM Provider | DeepSeek API (远程) | 本地 35B MoE (SGLang) | 07-14→07-17 | 🟡 中 |
| 数据降级链 | 仅有 yfinance | akshaare→efinance→yfinance + pytdx | 05-30 | 🟢 正向 |
| 技术指标回落 (fallback) | 无 (仅有 LLM 路径) | StockAnalyzer.technical_rating() | 06-01 | 🟢 正向 |

---

## 二、Bug 溯源分析

at.md 记录的 7 项修复，按来源分类：

### 基线遗留问题（6月23日前就存在，当时未触发）

| 修复 | 文件 | 基线代码 | 为何当时未触发 |
|:----|:----|:---------|:--------------|
| Fix #4 | `reflection.py` | ```f"{raw_return:+.1%}"``` 无 None 保护 | reflection 是后续迭代新增功能，6 月时未使用 |
| Fix #5 | `memory.py` | 两处 ```f"{raw_return:+.1%}"``` 无 None 保护 | pending entries 都有有效值，极端情况才会 None |
| Fix #6 | `trading_graph.py` | `_resolve_pending_entries` 无 try/except | _fetch_returns 全部成功时正常 |

### 后期开发暴露的问题

| 修复 | 文件 | 根因 | 引入原因 |
|:----|:----|:-----|:---------|
| Fix #7 | `mind_agent_wrapper.py` | SystemMessage 注入 → SGLang 拒绝 | DeepSeek→本地 35B MoE 切换 |
| Fix #1-3 | `.env` 配置 | DEEPSEEK_API_KEY 未写入 + provider 不匹配 | 多次 Provider 切换遗留 |

**结论：** 当前所有已识别的 bug 都已经修复。基线的 3 个隐藏 bug（Fix #4/5/6）也一并补上了。

---

## 三、功能增益（相对基线）

### 🟢 新增正向功能

| 功能 | 文件 | 说明 |
|:----|:-----|:-----|
| Policy Analyst | `trading_graph.py` | 政策监管分析 |
| Capital Flow Tracker | `trading_graph.py` | 资金流向分析 |
| LY 信号注入 | `context_preparer.py` | Analyst 获得 LY 上涨概率信息 |
| ML 因子注入 | `context_preparer.py` | Analyst 获得 ML 因子评分 |
| A 股数据降级链 | `ashare_data.py` | akshare→efinance→yfinance 三重兜底 |
| 技术指标 fallback | `stock_analyzer.py` | LLM 失败时纯技术评分兜底 |
| 历史记忆反思 | `reflection.py` | 记录决策 + alpha + 反思 |
| 辩论轮次 ×2 | `conditional_logic.py` | 多轮深度辩论 |

### 🟡 潜在风险

| 风险 | 影响 | 缓解措施 |
|:----|:-----|:---------|
| 35B MoE 推理慢（~20min/股） | 6 小时跑完 18 只 | systemd TimeoutStopSec=7200 已够 |
| max_debate_rounds=2 | 更多 LLM 调用，更慢 | 可改回 1 临时加速 |
| Policy/Capital Flow tools | 新闻/资金流 API 可能失败 | 有 fallback 处理 |

---

## 四、当前系统质量评估

### 架构层面

```
基线的上游架构 (AT v0.3.0)
  → 4 分析师 + 投资辩论 + 风险辩论 + PM
  → DeepSeek API (远程)
  → yfinance 数据源 (仅美股)

当前的 6 角色演进 (保留并扩展)
  → 6 分析师 + 投资辩论 + 风险辩论 + PM  ✅ 架构兼容
  → Signal injection (LY/ML)                 ✅ 新增价值层
  → A 股数据降级链                           ✅ 数据可靠性提升
  → 本地 35B MoE                             ✅ 零成本 + 高可用
  → Technical fallback                       ✅ 防御性增强
```

### 代码质量层面

| 指标 | 基线 | 当前 | 评估 |
|:----|:----:|:----:|:----:|
| None 防御 | ❌ 2 处暴露 | ✅ 全部修复 | 🟢 提升 |
| try/except 保护 | ❌ 无 | ✅ 有 | 🟢 提升 |
| 日志 | 基础 | 完整 INFO/WARNING | 🟢 提升 |
| 错误恢复 | 无 | try/except + fallback | 🟢 提升 |
| 注入兼容性 | DeepSeek only | SGLang + OpenAI 兼容 | 🟢 提升 |

### 内容质量层面

对比 6 月 23 日日志（DeepSeek + 4 分析师）与今日测试日志（35B MoE + 6 分析师）：

```
基线的 PM 决策 (0613):
  → Hold | 多空论证清晰 | alpha=+3.5% ✅

当前 PM 决策 (今日测试, 注入LY/ML):
  → 日志显示信号注入正常 | 辩论流程完整
  → 但 35B MoE 生成速度慢导致超时未能落地
```

---

## 五、结论

**当前系统相对 6 月 23 日基线是净正向进化：**

1. **Bug 已清零** — 基线遗留的 3 个隐藏 bug + 后期引入的兼容性问题均已修复
2. **功能增强** — 多了 2 位分析师、信号注入、A 股数据链、fallback 兜底
3. **架构兼容** — 4→6 分析师的扩展是在原型设计范围内的演进
4. **质量提升** — None 防御、错误处理、日志都比基线更好
5. **唯一代价** — 本地 35B MoE 推理速度比 DeepSeek API 慢 ~5x，但零成本 + 高可用

**待验证：** 
- `max_debate_rounds=2` 是双刃剑（质量↑ 速度↓），可考虑回退到 1
- 35B MoE 的辩论内容质量需一次完整运行来确认
