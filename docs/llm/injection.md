# 跨系统 LLM 信号注入

> 最后更新: 2026-06-24
> 涉及文件: `src/mind_agent_wrapper.py`, `systems/mind_TradingAgent/mind_tradingagent/`

---

## 一、什么是信号注入

三系统中，AT（多智能体辩论）是唯一直接调用 LLM 的子系统。LY 的量化信号和 ML 的因子分析结果，通过拼接到 AT 的 system prompt 中，让 LLM 在辩论时看到其他系统的判断，从而影响其推理。

```
ly_signal.json ──→ mind_agent_wrapper.py ──→ AT system prompt (Option A+)
ml_signal.json ──→ mind_agent_wrapper.py ──→ AT system prompt (Option A+)
```

## 二、注入内容

### LY 信号注入

从 `data/realtime/ly_signal.json` 读取每只股票的：
- 双模型（RF + LGB）上涨概率
- 信号标签（买入/观望/回避等）
- 置信度

注入到 AT 的 `_get_preloaded_context()` 中：

```
📊 外部量化模型信号:
【中航机载(600372)】RF上涨概率=60.5%, LGB信号=关注, 集成信号=关注
```

### ML 因子信号注入

从 `data/realtime/ml_signal.json` 读取：
- composite_score（综合因子得分）
- top_factors（前3个主导因子）
- l7_score（L7映射得分）

```
📊 外部因子模型信号:
【中航机载】综合得分=-0.208, L7=-0.715, 主导因子: momentum_reversal=-0.009, low_volatility=0.232
```

## 三、注入机制

| 参数 | 值 | 说明 |
|------|----|------|
| `should_inject` | `True` | 始终注入，不降级 |
| 注入方式 | Option A+ | SystemMessage(msg[0]) + AIMessage(msg[-1]) 双注入 |
| 零侵入 | ✅ | monkey-patch AT 的 `create_initial_state()` |
| 降级路径 | try/except ImportError | 仓库离线时自动跳过 |

## 四、LLM 提示词架构

| 子系统 | 提示词位置 | 说明 |
|--------|----------|------|
| AT system prompt | `default_config.py` → LLM API | 多智能体辩论基础提示 |
| AT agent 提示 | `agents/researchers/researcher.py` | 分析师提示词 |
| AT risk 提示 | `agents/risk/` | 风险分析师提示词 |
| AT trader 提示 | `agents/portfolio_manager.py` | 投资经理提示词 |
| ML system prompt | `src/core/pipeline.py` | 整点分析 LLM 提示 |
| ML prompt 全集 | `prompts.md` | 详细 ML 提示词文档 |

## 五、相关文档

| 文档 | 说明 |
|------|------|
| `prompts.md` | ML 子系统系统提示词全集 |
| `at-agents.md` | AT 多智能体辩论提示词（待补充） |
| `roadmap.md` | LLM 改进路线图 |
| `data-chain/overview.md` | 数据链总览 |
