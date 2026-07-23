"""
Prompt 配置 — A/B 版本选择器

B 版精简原则:
  - 移除教育性重复文本（LLM 学会后不再需要）
  - 压缩评分标准中的校准注释
  - 移除因子剖面中的解释文本（只看 z-score 即可）
  - 策略指令用 compact 模式

用法:
  USE_COMPACT_PROMPT=1 python ...  # 启用 B 版
"""
from __future__ import annotations

import os

_B = os.environ.get("USE_COMPACT_PROMPT", "") == "1"


def is_compact() -> bool:
    return _B


# ── 评分标准 ──

SCORING_CRITERIA_A = """
## 评分标准

- **80-100**: 强烈看多 — 多头排列 + 量价配合 + 无利空 + 主力资金流入 + 筹码集中。\
这类信号可参与，但需设置止损。
- **60-79**: 看多 — 技术面偏多 + 基本面正常。可小仓参与。
- **40-59**: 中性 — 方向不明确或多空因素平衡的震荡走势。不参与。
- **0-39**: 看空 — 技术面/基本面偏空。不参与。

{@calibration 598样本回测校准}:
- **52/49 阈值体系**：sentiment_score ≥ 52 看多，≤ 49 看空，50-51 为 flat zone（降权 0.5）。
- 校准目标：在看多区间准确率 74.6% 的前提下，覆盖率不低于 98%。
- **该阈值经过系统性回测，关闭 flat zone 会劣化准确率。**
- **op_advice 仅作为文本解释**，不参与评分计算。最终决策依据 sentiment_score。
- **多技能冲突处理**：当不同激活技能给出矛盾信号时，以综合所有工具返回的实际数据为准，\
避免单一技能的主导（S5 修复 2026-06-08）。
- **LLM 大数值幻觉防护**：sentiment_score、target_price、stop_loss 等数值必须在实际数据范围内，\
若 tool 返回的 MAX 值为 100，评分不应超过 100（v3.1 @calibration 2026-06-23）。
"""

SCORING_CRITERIA_B = """
## 评分标准

- 80-100: 强烈看多 → 可参与，设止损
- 60-79: 看多 → 小仓参与
- 40-59: 中性 → 不参与
- 0-39: 看空 → 不参与

方向阈值: >=52 看多, <=49 看空, 50-51 flat(降权0.5)
op_advice 仅作文本解释，不参与评分。
"""

# ── 操作护栏 ──

ACTION_GUARDRAILS_A = """
## 操作护栏 (必须遵守)

1. **不追高** — 股价偏离 MA5 超过 5% 时不得建议买入。
2. **仓位匹配评分** — sentiment_score ≤ 39 (看空) 时不得建议买入，op_advice 必须与 sentiment_score 方向一致。
3. **不频繁交易** — 单只股票同一交易日不反复买卖（避免 whipsaw）。
4. **止损优先** — 任何时候触发止损条件，操作建议必须优先反映风控。
5. **数据验证** — 所有数值必须基于工具返回的实际数据，不得编造。
"""

ACTION_GUARDRAILS_B = ACTION_GUARDRAILS_A  # 已精简，不改

# ── 因子解释文本 ──

FACTOR_INTERPRETATIONS = {} if _B else None  # None → 使用 engine 内置的完整版
# B 版: 不在因子剖面中追加解释，仅显示 z-score

# ── 信号关联说明 ──

SIGNAL_CORRELATION_A = """
> **成交量信号的多机制区分（重要）**：成交量衍生的多个信号并非相互矛盾——它们测量的是**不同的市场机制**。\
**逆向/情绪类**（turnover_sentiment 换手率情绪因子、emotion_cycle 情绪周期策略）将高成交量解读为"散户追涨/情绪过热"→ 偏空/谨慎；\
**趋势跟随/量价确认类**（volume_breakout 放量突破策略、volume_status 放量上涨、bottom_volume 底部放量策略、放量拉升监控）将高成交量解读为"突破确认/主力介入/恐慌出清"→ 偏多。\
这两种解读并不互斥——高成交量同时意味着"散户参与度高"（逆向偏空）和"趋势获得确认"（趋势偏多）。请在分析中分别评估这两种机制，而非将其视为矛盾信号。
"""

SIGNAL_CORRELATION_B = ""  # B 版完全移除

# ── 策略指令模式 ──

SKILL_INSTRUCTION_MODE: str = "full" if not _B else "compact"
# "full": 完整的 instructions 文本
# "compact": 单行摘要（~100 chars/个）


def get_scoring_criteria() -> str:
    return SCORING_CRITERIA_B if _B else SCORING_CRITERIA_A


def get_action_guardrails() -> str:
    return ACTION_GUARDRAILS_B if _B else ACTION_GUARDRAILS_A


def get_signal_correlation() -> str:
    return SIGNAL_CORRELATION_B if _B else SIGNAL_CORRELATION_A


def get_skill_instruction_mode() -> str:
    return SKILL_INSTRUCTION_MODE


def should_include_factor_interpretations() -> bool:
    """B 版不移除 factor 解释，但可选关闭"""
    return not _B
