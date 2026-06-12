# AT 子系统数据注入实施报告

> 实施日期: 2026-06-12
> 相关研究报告: `docs/research/at-optimization-oracle-c1skill-review.md`
> 涉及提交: `4010889`, `1063af2`, `b61eeac`, `21b50bd`, `c79cace`, `fdadcec`

---

> **零侵入说明**: 本次实施虽然拥有规则豁免权，但实际**未对 AT 子系统（systems/mind_TradingAgent/）做任何代码修改**。
> 所有改动均在 Fusion 引擎层的 `src/mind_agent_wrapper.py` 中通过运行时 monkey-patch 实现。
> 即使没有豁免规则，本次实施也是合规的。

## 一、背景

### 1.1 问题

mind_TradingAgent (AT) 子系统在融合回测中准确率仅 28%（100 个已匹配样本），显著低于随机（p=0.0000）。

### 1.2 根因诊断（数据审计）

AT 的数据源链为 `akshare → yfinance → alpha_vantage`。akshare 在运行时持续 `RemoteDisconnected`，导致降级到 yfinance。yfinance 对 A 股仅提供基础 OHLCV，缺失：

- **中文新闻**（Agent 看到英文新闻，对 A 股近乎无用）
- **资金流向/北向资金**（完全缺失）
- **基本面**（PE/PB 稀疏）
- **雪球/东方财富情绪**（完全缺失）
- **LY 量化信号**（上涨概率、L7 得分、模型分歧度）
- **ML 因子信号**（综合评分、因子贡献）

结论：AT 的 10 个 Agent 实质上在**闭眼辩论**，仅凭借裸 K 线数据做多空判断。28% 准确率的主因是数据断层，而非 Agent 辩论能力。

### 1.3 规则豁免

本课题不受"零侵入"规则约束，允许直接修改 mind_TradingAgent 子系统代码。

---

## 二、实施方案（Option A+）

### 2.1 设计方案

基于 Oracle 分析 + c1skill 审查，选择增量增强方案（Option A+）：

```
方案对比:
  Option A (增强注入)       — 只加 LY 信号到 AIMessage          → 权威层级不够
  Option B (禁用自取数据)    — 改 AT 内部工具调用逻辑             → 侵入性大
  Option C (直接改 system prompt) — 改 4 个 Analyst 文件         → 工作量 2-3 天
  Option A+ (双注入)         — system prompt + AIMessage 同时注入 → 1 天, 风险最低
```

### 2.2 核心改动

**文件**: `src/mind_agent_wrapper.py`

| 改动 | 说明 | 行数 |
|------|------|------|
| `_load_ly_signals_for_at()` | 新增方法，读取 ly_signal.json + ly_alpha_signal.json + prob_up_log.csv，对齐 pipeline.py 的 `_load_ly_signals()` 逻辑 | ~50 |
| ML 因子信号读取 | 在 `_get_preloaded_context()` 中读取 ml_signal.json，提取 composite_score/l7_score/composite_label/top-3 factors | ~20 |
| `should_inject = True` | 无条件注入，不再依赖 `fast_degrade` 条件 | 1 行 |
| **Option A+ 双注入** | 同时注入 SystemMessage（头部，最高权威层级）+ AIMessage（尾部，详细上下文） | ~30 |
| `_get_preloaded_context()` 返回扩展 | 新增 `ly_signals_context` 和 `ml_factor_context` 键 | +2 |

### 2.3 注入链路

```
最终实现的数据流:

ly_signal.json ──┐
ly_alpha_signal  ─┤  _load_ly_signals_for_at()  ──┐
prob_up_log.csv ──┘                                │
                                                   ├──→ _get_preloaded_context()
ml_signal.json ──→ ML因子信号读取  ────────────────┤        │
UnifiedCache ──→ OHLCV+技术指标  ──────────────────┤        │
ML stock_analysis.db ──→ ML分析/基本面/新闻 ────────┘        │
                                                             │
                    ┌────────────────────────────────────────┘
                    ▼
         _injected_create()  ──  monkey-patch create_initial_state
              │
              ├── SystemMessage(content=注入数据) → messages.insert(0)
              │    (位于 system prompt 之后、所有消息之前 → 最高权威层级)
              │
              └── AIMessage(content=注入+详细数据) → messages.append()
                   (位于消息列表末尾、工具结果之前 → 详细上下文)

         AT 10 Agent 辩论开始
              │
              ▼
         TradingAgentsGraph.propagate()
              │
              ▼
         final_trade_decision (Buy/Overweight/Hold/Underweight/Sell)
              │
              ▼
         at_signal.json (文件交换区)
```

### 2.4 注入数据格式

#### LY 量化信号（Markdown 表格）

```
| 综合上涨概率 | 45.2% | RF+LGB 双模型集成 |
| RF 上涨概率 | 42.1% | RandomForest（15+ 技术指标） |
| LGB 上涨概率 | 48.3% | Alpha158 LightGBM（158 因子） |
| L7 得分(RF) | +0.33 | 范围[-3,+3] 正值偏多 |
| 综合置信度 | 弱 | 强(≥70%) 中(55-70%) 弱(<55%) |
| 模型分歧 | 6.2% | 低分歧 |
```

#### ML 因子信号

```
综合评分=-0.102 | L7=-0.455 | 标签=中性 | 前三因子: turnover_sentiment=2.12 | volatility_ratio=1.53 | max_effect=0.87
```

---

## 三、P0 前置工作

### 3.1 统计显著性检验

**文件**: `scripts/at_significance.py`

基于 bt_results.db 的 100 条已匹配预测，对 AT 做二项检验 + 混淆矩阵 + 个股分解。

核心发现：

| 指标 | 值 | 判定 |
|------|-----|------|
| AT 整体准确率 | 28/100 = 28.0% | 显著低于随机 (p=0.0000) |
| 看空准确率 | 28/49 = 57.1% | 不显著优于随机 (p=0.20) |
| 看多准确率 | 0/6 = 0.0% | 全部错误 |
| 中性准确率 | 0/45 = 0.0% | 全部无方向判断 |
| 市场下跌率 | 63% | 始终看空优于 AT |

混淆矩阵：

| 预测\实际 | 涨(33) | 跌(63) | 平(4) |
|----------|--------|--------|-------|
| 看多(6) | 0 | 6 | 0 |
| 看空(49) | 18 | 28 | 3 |
| 中性(45) | 15 | 29 | 1 |

### 3.2 权重调整

**文件**: `config/settings.yaml`

| 系统 | 旧权重 | 新权重 | 变动 |
|------|--------|--------|------|
| ly (lynx_vnpy) | 0.30 | 0.36 | +0.06 |
| ml (MindLynx-Aistock) | 0.40 | 0.48 | +0.08 |
| at (TradingAgent) | **0.25** | **0.10** | **-0.15** |
| mf (ml_factor) | 0.05 | 0.06 | +0.01 |

backtest.initial_weights 同步更新，weight_search_range 相应调整。

---

## 四、文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/mind_agent_wrapper.py` | 修改 | LY/ML 信号注入 + Option A+ 双注入 |
| `scripts/verify_at_injection.py` | 新增 | 注入验证脚本（数据文件检查 + 上下文加载验证） |
| `scripts/at_significance.py` | 新增 | AT 统计检验 + 混淆矩阵 + 个股分解 |
| `config/settings.yaml` | 修改 | AT 权重 0.25→0.10，其余按比例重新分配 |
| `docs/research/at-optimization-oracle-c1skill-review.md` | 修改 | 新增第九章数据审计 + 更新 P0 结果 + 更新 c1skill 结论 |

### 提交历史

```
4010889 feat(at): LY+ML信号注入AT Agent + Option A+双重注入
1063af2 docs: AT研究报告补充数据审计结论(运行时数据断层) + 更新P0结果
b61eeac P0: AT统计检验+降权到0.10
21b50bd docs: c1skill论证补充豁免规则影响修正
c79cace docs: AT研究报告增加规则豁免声明(零侵入例外)
fdadcec docs: AT研究报告补充c1skill独立审查观点
aa125f0 fix: 重要性阈值过滤+每日情报格式优化
a1187c0 feat(ml): LY量化信号注入ML系统LLM上下文
```

---

## 五、验证结果

### 5.1 数据文件检查

```
scripts/verify_at_injection.py --code 601801

✅ ly_signal: 存在 (601801: score=0.333, signal=谨慎看空)
✅ prob_up_log: 存在 (130 rows, latest: 2026-06-12)
✅ ml_signal: 存在
```

### 5.2 上下文加载验证

```
✅ LY 信号: 已加载 (269 chars)
   | 综合上涨概率 | 45.2% | RF+LGB 双模型集成 |
   | L7 得分(RF) | +0.33 | ... |
   
✅ ML 因子: 已加载 (153 chars)
   综合评分=-0.102 | L7=-0.455 | 标签=中性 | 前三因子: ...

✅ 行情: OHLCV (最近5天) + 技术指标 (MA/RSI/MACD/BOLL/ATR)
✅ 基本面: PE=13.87 | PB=0.86 | 板块=出版/传媒/安徽
```

---

## 六、监控与后续

### 6.1 待观测指标

| 指标 | 基线 | 目标 | 观测周期 |
|------|------|------|---------|
| AT 方向准确率 | 28% | > 35% (统计显著) | 5 个交易日 |
| AT 响应率 | ~60% (48/69) | > 85% | 每次运行 |
| LY 信号文件时效 | - | < 36h | 每次注入前 |
| injection_hit_rate | 0 | ≥ 2 Agent 引用注入数据 | debug 追踪 |

### 6.2 后续步骤

1. **5 交易日后**: 运行 `scripts/at_significance.py` 对比注入前后的准确率变化
2. **如果准确率仍 < 35%**: 考虑升级到 Option C（直接改 4 个 Analyst 的 system prompt，让每个 Agent 分别接收与其角色匹配的信号）
3. **如果准确率 > 40%**: 证明数据断层是主因，可考虑扩展 at_signal.json 字段（加数据源标记、置信度），实现运行时数据源可追溯

### 6.3 回滚方案

```bash
# 方案 1: 完整回滚
git checkout 1063af2 -- src/mind_agent_wrapper.py

# 方案 2: 仅回滚注入逻辑（保留 LY 信号读取）
# 把 should_inject = True 改回 data_check.get("fast_degrade", False)
# 然后恢复旧的 _injected_create 函数
```

---

## 七、相关文档索引

| 文档 | 内容 |
|------|------|
| `docs/research/at-optimization-oracle-c1skill-review.md` | AT 优化全报告（含 c1skill 审查 + 数据审计） |
| `docs/research/at-data-injection-implementation.md` | **本文档 — 实施报告** |
| `docs/current-state.md` | 项目当前状态快照 |
| `docs/backtest.md` | 回测系统文档 |

---

## 八、术语

| 缩写 | 全称 |
|------|------|
| AT | mind_TradingAgent（多智能体辩论系统） |
| LY | lynx_vnpy（RandomForest+LGB 量化信号） |
| ML | MindLynx-Aistock（因子+LLM 分析系统） |
| L7 | 7 级决策空间 [-3, +3] |
| Option A+ | 同时注入 SystemMessage(头部) + AIMessage(尾部) 的方案 |
| fast_degrade | akshare+efinance 失败、yfinance 可用时触发的降级模式 |
