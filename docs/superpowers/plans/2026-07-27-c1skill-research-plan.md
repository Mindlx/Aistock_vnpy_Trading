# c1skill 研究计划：Prompt 全链条审计 + ML 个股评分校准

> **依据**: 2026-07-27 系统诊断发现 TA 定时器 5 只股票 100 分异常，其中蓝天燃气(605368) 回测准确率仅 39.6% 仍得满分，暴露评分映射过于粗糙。

---

## 课题一：Prompt 全链条审计

### Stage 0 — 原架构理解

**目标**: 绘制完整的 prompt 流向图，理清每条 prompt 的触发条件、内容来源、格式占位符。

**执行步骤**:
1. 在 `systems/MindLynx-Aistock/src/` 下搜索所有 `"""` 三引号字符串（排除 import/docstring）
2. 识别每个 prompt 的名称、位置、用途（system/chat/user/tool）
3. 追溯占位符 `{placeholder}` 的来源（哪些代码填充了它们）
4. 归类: 分析器 prompt / Agent prompt / 聊天 prompt / 工具 prompt
5. 追溯 git 历史: 每条 prompt 的创建和修改记录

**产出**: prompt 流向地图（文件:line → prompt名称 → 占位符来源）

### Stage 1 — 问题定义

根据 7/23 诊断结论，可能的问题:
- `AGENT_SYSTEM_PROMPT` 和 `LEGACY_DEFAULT_AGENT_SYSTEM_PROMPT` 冗余（已部分修复）
- `SCORING_CRITERIA_A/B` 共存但 B 从未启用
- 评分标准在不同 prompt 中是否存在不一致？
- `CHAT_SYSTEM_PROMPT` 和 `AGENT_SYSTEM_PROMPT` 的评分标准是否对齐？

### Stage 2 — 跨学科证据

| 领域 | 著作 | 搜索目标 |
|:-----|:-----|:---------|
| 认知科学 | Kahneman TFS | 锚定效应：评分标准锚定值（80-100）是否诱导 LLM 给高分 |
| 量化管理 | Carver Systematic Trading | prompt 作为信号源的方差控制，冗余 prompt 的 IC 衰减 |

### Stage 3-7

审计完成后，输出:
- 建议保留/合并/删除的 prompt 清单
- 评分标准统一方案（A/B 版去留决策）
- prompt 版本控制策略

---

## 课题二：ML 个股评分校准

### Stage 0 — 原架构理解

当前评分数据流:

```
TA timer (10:10/13:30)
  → scripts/run_daily.py --run-ta
    → fusion.json (fusion_score [-3, +3])
    → sentiment_score = int(50 + fusion_score × 16.67)  ← 问题行
    → 写入 analysis_history.sentiment_score

整点分析 (11:00/14:00)
  → pipeline.py _analyze_with_agent
    → LLM 生成 sentiment_score (0-100)
    → 可选因子锚定（divergence >= 20 时融合）
    → 写入 analysis_history.sentiment_score

实时监控 (09:40-14:30 每29min)
  → realtime_monitor.py
    → 从 analysis_history 读取最新 sentiment_score
    → 显示在预警消息中
```

**问题行**: `run_daily.py:316` — 线性映射 `50 + fusion_score × 16.67` 是纯数学映射，没有考虑股票级历史准确率校准。

### Stage 1 — 精确定义问题

**问题卡片**: 对于历史准确率低的股票（如 605368 蓝天燃气 39.6%），fusion_score=3.0 仍映射到 100 分，导致过信推送。影响范围: TA 定时器输出 → analysis_history → 实时监控推送。

严重程度: **MEDIUM**（不影响交易执行，只影响评分显示）

### Stage 2 — 跨学科证据

| 领域 | 著作 | 支持论点 |
|:-----|:-----|:---------|
| 统计学习 | Hastie ESL | 预测校准（Prediction Calibration）：模型输出的置信度应与实际频率一致。准确率 40% 的股票不应给出 100 分置信 |
| 量化管理 | Grinold & Kahn APM | IC 衰减：信号强度应乘以 IC（信息系数）来校准预期收益。历史 IC 低的股票应降权 |
| 行为金融 | Montier Behavioural Investing | 过度自信偏误：模型对高波动股票的预测往往过度自信，需根据历史 MSE 校准 |
| 量化 ML | López de Prado AFML | 回测过拟合：个股级准确率可能过拟合，应使用交叉验证准确率而非全体样本 |

### Stage 4 — 反方论据

**反方**: 准确率低的股票可能只是样本量少（仅 48 条预测），校准会过度惩罚。
→ **修正**: 校准公式应包含样本量权重（贝叶斯收缩），小样本向全局均值回归。

### Stage 5 — 修复方案设计

#### Phase 1（零风险，立即做）
在 `run_daily.py:316` 加入简单校准:
```python
# 从 bt_results.db 读取该股票的历史准确率
stock_acc = lookup_stock_accuracy(code)  # 0.0 ~ 1.0
if stock_acc is not None and stock_acc < 0.5:
    confidence_discount = 0.5 + stock_acc  # 40% → 0.9, 30% → 0.8
    fusion_score *= confidence_discount
```
工作量: 半小时，单文件改动。

#### Phase 2（经验诊断脚本）
写一个诊断脚本 `scripts/diagnose_scoring.py`:
- 扫描所有 analysis_history 的 sentiment_score
- 对照 bt_results.db 的真实次日涨跌幅
- 输出每只股票的**评分-准确率校准曲线**
- 识别系统性的过信/欠信股票

#### Phase 3（条件逻辑改动）
根据 Phase 2 的数据，设计校准函数:
- 个股级准确率 → 贝叶斯收缩到全局均值
- 样本量权重（N < 30 时向均值收缩）
- 融合 score 的范围做动态 clamp

---

## 执行顺序

1. **先做 Phase 1**（零风险补丁，今天可做）
2. **课题一 Stage 0**（全量 prompt 地图，1-2 天）
3. **课题二 Phase 2**（诊断脚本，半天）
4. **根据诊断结果做 Phase 3**（需 Phase 2 数据支撑方执行）

---

## 开始条件

准备好后，先做哪个？
- **课题一**: 启动全量 prompt 扫描
- **课题二 Phase 1**: 先在 run_daily.py 加准确率折扣（30分钟）
- **课题二 Phase 2**: 写 diagnose_scoring.py 诊断脚本
