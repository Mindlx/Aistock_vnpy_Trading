"""
Shared prompt fragments used across multiple system prompts.

Single source of truth for content that appears identically in
analyzer.py, executor.py, and decision_agent.py.  Edit here to
update everywhere.

Ref: docs/cross_layer_audit_lessons.md (ARCH-3 fix)

Calibration note (2026-06-07):
  Thresholds aligned with backtest-validated 52/49 split:
  - score >= 52 → bullish (74.6% accuracy, 98% coverage)
  - score <= 49 → bearish (92.8% accuracy for 31-49 range)
  - score 50-51 → flat zone (extremely narrow, only 2% of samples)

  Asymmetry warning: bearish signals are significantly more reliable
  than bullish signals in current market regime (downtrend_mid_vol).
  - 31-49 range: 92.8% direction accuracy
  - 52-59 range: 56.2% direction accuracy
  - 60-79 range: 38.2% direction accuracy (extreme scores LESS reliable!)
"""

SCORING_CRITERIA = """
## 评分标准

### 强烈买入（80-100分）：
- ✅ 多个激活技能同时支持积极结论
- ✅ 上行空间、触发条件与风险回报清晰
- ✅ 关键风险已排查，仓位与止损计划明确
- ✅ 重要数据和情报结论彼此一致
- 📊 **参考锚点**：现价靠近支撑位且量比配合（上涨量比>1.2 或 缩量回踩<0.8）

### 买入/加仓（52-79分）：
- ✅ 主信号偏积极，但仍有少量待确认项
- ✅ 允许存在可控风险或次优入场点
- ✅ 需要在报告中明确补充观察条件
- 📊 **参考锚点**：乖离率适中（偏离 MA5 < 5%）或处于支撑/压力区间中部
- ⚠️ **注意**：52-59分区间准确率有限(~56%)，需更强的技术面确认

### 观望/谨慎（50-51分）：
- ⚠️ 信号分歧较大，或缺乏足够确认
- ⚠️ 风险与机会大致均衡
- ⚠️ 更适合等待触发条件或回避不确定性
- 📊 **参考锚点**：价格处于压力位附近、量价背离，或乖离率偏大（>5%）
- ⚠️ **严格限制**：这是唯一的中性区间，score 50或51分之外请在方向性区间内评分

### 卖出/减仓（0-49分）：
- ❌ 主要结论转弱，风险明显高于收益
- ❌ 触发了止损/失效条件或重大利空
- ❌ 现有仓位更需要保护而不是进攻
- 📊 **参考锚点**：跌破关键支撑位、放量下跌（量比>1.5且价格收阴）
- ✅ **高可信度区间**：score 31-49区间准确率达93%，可果断执行

> 📊 锚点条件为参考性指标，非硬性门槛。当锚点条件与技能综合判断冲突时，以技能判断为主。
>
> 📊 系统校准提示：当前系统在识别下跌信号时（0-49分）比识别上涨信号时（52-100分）更准确。
> 评分时应基于实际市场分析，不要因为"看空更准"就刻意偏向看空或看多。

### 多技能冲突处理（S5 修复）：
- 当多个激活技能对同一只股票给出不同方向的评分调整时，以**多数共识**（≥2/3 技能同方向）为主要参考；若无明显多数，取**更保守的方向**（偏向观望/减仓）。
- 优先级数字越小越优先，但优先级不是绝对权威——低优先级技能若基于更可靠的数据（如基本面、事件面），可以合理挑战高优先级技能。
- 技能评分的累计调整幅度建议控制在 ±35 分以内（S3 修复），超出部分需在报告中明确注明依据。
"""

ACTION_GUARDRAILS = """
## 可操作性与稳定性约束

- 不得仅因为单日涨跌或评分跨线就在"买入/卖出"之间剧烈切换。
- 操作建议应与综合评分对齐：评分≥52时优先给出"买入"或"加仓"，
  评分≤49时优先给出"减仓"或"卖出"，评分50-51时输出"持有/观望"。
- 评分≥52且有支撑位确认时，可给出买入；接近压力且资金流出时不得追买。
- 评分≤49且有技术面确认时，可给出卖出/减仓；无明确风险信号时避免过度反应。
- 注意：score 60分以上并不比score 52-59分更可靠，切勿仅因高分就过度看好。
"""
