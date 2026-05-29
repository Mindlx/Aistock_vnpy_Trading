<p align="center">
  <img src="assets/TauricResearch.png" style="width: 60%; height: auto;">
</p>

<div align="center" style="line-height: 1;">
  <a href="https://arxiv.org/abs/2412.20138" target="_blank"><img alt="arXiv" src="https://img.shields.io/badge/arXiv-2412.20138-B31B1B?logo=arxiv"/></a>
  <a href="https://discord.com/invite/hk9PGKShPK" target="_blank"><img alt="Discord" src="https://img.shields.io/badge/Discord-TradingResearch-7289da?logo=discord&logoColor=white&color=7289da"/></a>
  <a href="./assets/wechat.png" target="_blank"><img alt="WeChat" src="https://img.shields.io/badge/WeChat-TauricResearch-brightgreen?logo=wechat&logoColor=white"/></a>
  <a href="https://x.com/TauricResearch" target="_blank"><img alt="X Follow" src="https://img.shields.io/badge/X-TauricResearch-white?logo=x&logoColor=white"/></a>
  <br>
  <a href="https://github.com/TauricResearch/" target="_blank"><img alt="Community" src="https://img.shields.io/badge/Join_GitHub_Community-TauricResearch-14C290?logo=discourse"/></a>
</div>

---

> **⚠️ 重要声明：Mind TradingAgent 是 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) 的一个独立分支，并非官方项目。** 
> 
> 此项目（mind_TradingAgent）基于 TradingAgents v0.2.5 进行私有化定制与二次开发。原项目由 Tauric Research 团队维护，采用 MIT 许可证。
> 本分支的修改和定制内容由 Mindlx 负责，与原始项目无关。

---

# Mind TradingAgent: 私有定制多智能体 LLM 金融交易框架

## 更新日志

- [2026-05] **基于 TradingAgents v0.2.5 分支**，重命名为 `mind_tradingagent`，开始私有定制化开发。

---

本框架将复杂的交易任务分解为多个专业化角色，确保系统在市场分析和决策中具备稳健、可扩展的能力。

### 分析师团队
- 基本面分析师：评估公司财务和业绩指标，识别内在价值和潜在风险
- 情绪分析师：聚合新闻头条、StockTwits 和 Reddit 讨论，评估短期市场情绪
- 新闻分析师：监控全球新闻和宏观经济指标，解读事件对市场状况的影响
- 技术分析师：利用技术指标（如 MACD 和 RSI）检测交易模式和预测价格走势

### 研究员团队
- 由看涨和看空研究员组成，对分析师团队的见解进行批判性评估。通过结构化辩论，平衡潜在收益与内在风险。

### 交易员智能体
- 综合分析师和研究员报告，做出知情的交易决策，确定交易的时机和规模。

### 风险管理和投资组合管理
- 通过评估市场波动性、流动性等风险因素，持续评估投资组合风险。风险管理团队评估和调整交易策略，为投资组合经理提供评估报告以做出最终决策。

---

## 安装与 CLI

### 安装

克隆此仓库：
```bash
git clone https://github.com/Mindlx/mind_TradingAgents.git
cd mind_TradingAgent
```

创建虚拟环境（推荐 Python 3.10+）：
```bash
conda create -n mind_tradingagent python=3.10
conda activate mind_tradingagent
```

安装包及其依赖：
```bash
pip install .
```

### Docker

或者使用 Docker 运行：
```bash
cp .env.example .env  # 添加你的 API key
docker compose run --rm mind-tradingagent
```

### 所需 API

支持多种 LLM 提供商。为你选择的提供商设置 API key：

```bash
export OPENAI_API_KEY=...          # OpenAI (GPT)
export GOOGLE_API_KEY=...          # Google (Gemini)
export ANTHROPIC_API_KEY=...       # Anthropic (Claude)
export DEEPSEEK_API_KEY=...        # DeepSeek
export DASHSCOPE_API_KEY=...       # Qwen — International
export DASHSCOPE_CN_API_KEY=...    # Qwen — China
export ZHIPU_API_KEY=...           # GLM via Z.AI (international)
export ZHIPU_CN_API_KEY=...        # GLM via BigModel (China)
export MINIMAX_API_KEY=...         # MiniMax — Global
export MINIMAX_CN_API_KEY=...      # MiniMax — China
export ALPHA_VANTAGE_API_KEY=...   # Alpha Vantage
```

### CLI 使用

启动交互式 CLI：
```bash
mind-tradingagent          # 安装后的命令
python -m cli.main         # 或直接从源码运行
```

## Python 包使用

在自己的代码中使用 `mind_tradingagent` 模块：

```python
from mind_tradingagent.graph.trading_graph import TradingAgentsGraph
from mind_tradingagent.default_config import DEFAULT_CONFIG

ta = TradingAgentsGraph(debug=True, config=DEFAULT_CONFIG.copy())

# 前向传播
_, decision = ta.propagate("NVDA", "2026-01-15")
print(decision)
```

自定义配置：

```python
from mind_tradingagent.graph.trading_graph import TradingAgentsGraph
from mind_tradingagent.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "openai"
config["deep_think_llm"] = "gpt-5.4"
config["quick_think_llm"] = "gpt-5.4-mini"
config["max_debate_rounds"] = 2

ta = TradingAgentsGraph(debug=True, config=config)
_, decision = ta.propagate("NVDA", "2026-01-15")
print(decision)
```

详细配置选项请参见 `mind_tradingagent/default_config.py`。

## 持久化与恢复

### 决策日志

每次运行的决策会追加到 `~/.mind_tradingagent/memory/trading_memory.md`。

### 检查点恢复

检查点存储在 `~/.mind_tradingagent/cache/checkpoints/<TICKER>.db`。

```bash
mind-tradingagent analyze --checkpoint
mind-tradingagent analyze --clear-checkpoints
```

## 归属与引用

此项目是 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) 的分支。感谢 Tauric Research 团队的杰出工作。

请引用原始工作：

```
@misc{xiao2025tradingagentsmultiagentsllmfinancial,
      title={TradingAgents: Multi-Agents LLM Financial Trading Framework}, 
      author={Yijia Xiao and Edward Sun and Di Luo and Wei Wang},
      year={2025},
      eprint={2412.20138},
      archivePrefix={arXiv},
      primaryClass={q-fin.TR},
      url={https://arxiv.org/abs/2412.20138}, 
}
```
