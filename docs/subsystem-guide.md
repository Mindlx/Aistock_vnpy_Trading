# 子系统对接指南

## 概述

三个子系统以 `systems/` 目录下的代码拷贝形式存在，融合引擎通过**零侵入**方式读取其输出。本指南说明各系统的数据如何流入融合引擎。

---

## 1. lynx_vnpy — 量化信号系统

### 数据流

```
lynx_signal.py
├── fetch_daily_bars(code)      → 新浪财经 API 获取日K线
├── compute_features(df)        → 15个技术指标
├── train_model(df, code)       → RandomForest 训练（首次运行）
├── predict_signal(df, code)    → 预测 + 信号生成
└── 返回 dict: {code, name, signal, prob_up, rsi, ...}
```

### 融合引擎对接方式

**文件**: `src/data_loader.py` → `LynxDataLoader`

```python
# 零侵入：直接用 sys.path 导入
import lynx_signal
df = lynx_signal.fetch_daily_bars("601801")
df_feat = lynx_signal.compute_features(df)
sig = lynx_signal.predict_signal(df_feat, "601801", "皖新传媒")
# sig = {"code":"601801", "signal":"🟢 买入", "prob_up":72.0, ...}
```

不调用 `lynx_signal.run()`（会打印+推送），只调用其导出函数。

### 预训练模型

`systems/lynx_vnpy/models/` 下有 10 只股票各 2 个文件：
- `{code}_model.pkl` — RandomForest 模型
- `{code}_scaler.pkl` — StandardScaler 参数

首次运行时会自动训练。已有模型直接加载。

---

## 2. MindLynx-Aistock — AI 分析系统

### 数据流

```
main.py --schedule
└── StockAnalysisPipeline.run()
    ├── 数据获取 (akshare/efinance)
    ├── 12因子计算
    ├── 15策略匹配
    ├── LLM推理 → AnalysisResult
    ├── 保存到 SQLite DB (data/stock_analysis.db)
    └── 生成报告 → reports/report_YYYYMMDD.md
```

### 融合引擎对接方式

**文件**: `src/data_loader.py` → `MindLynxDataLoader`

```python
# 零侵入：只读文件，不导入 Python 代码
loader = MindLynxDataLoader()
signals = loader.load_by_date("2026-05-29")
# 解析 reports/report_20260529.md 中的信号行:
#   🟡 **皖新传媒(601801)**: 持有 | 评分 52 | 震荡偏多
#   ⚪ **古麒绒材(001390)**: 观望 | 评分 46 | 看空
#   🔴 ***ST网达(603189)**: 卖出 | 评分 34 | 强烈看空
```

### 报告的两种文件名格式

- `reports/report_2026-05-29.md`（带横杠）
- `reports/report_20260529.md`（不带横杠）

---

## 3. mind_TradingAgent — 多智能体系统

### 数据流

```
TradingAgentsGraph.propagate("601801.SS", "2026-05-28")
├── Market Analyst → get_stock_data() → yfinance
├── Sentiment Analyst → get_news()
├── News Analyst → get_global_news()
├── Fundamentals Analyst → get_fundamentals()
├── Bull vs Bear Researcher Debate
├── Research Manager → investment_plan
├── Trader → trader_investment_plan
├── Risk Management Debate (Aggressive/Neutral/Conservative)
└── Portfolio Manager → final_trade_decision
    ├── rating: Buy/Overweight/Hold/Underweight/Sell
    └── executive_summary + investment_thesis
```

### 融合引擎对接方式

**文件**: `src/data_loader.py` → `TradingAgentDataLoader`

```python
# 方式1: 读取已生成的 JSON 日志
loader = TradingAgentDataLoader()
decision = loader.load_by_stock_and_date("601801", "2026-05-29")
# decision = {"rating": "Buy", "price_target": 15.5, ...}

# 方式2: 通过 wrapper 批量驱动
wrapper = MindTradingAgentWrapper()
results = wrapper.run_batch(["601801", "001390"], "2026-05-29")
```

### A股代码格式

yfinance 要求 A 股代码带交易所后缀：

| 交易所 | 后缀 | 示例 |
|--------|------|------|
| 上海 | `.SS` | `601801.SS` |
| 深圳 | `.SZ` | `001390.SZ` |

### 数据缓存

TradingAgent 运行后会在用户 home 目录生成：
```
~/.mind_tradingagent/logs/{TICKER}/MindTradingAgentStrategy_logs/
└── full_states_log_{DATE}.json
```

融合引擎的 `TradingAgentDataLoader` 读取此目录。

---

## 系统间依赖关系

```
                    ┌──────────────────┐
                    │  融合引擎         │
                    │  (src/)           │
                    └────────┬─────────┘
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
   lynx_vnpy          MindLynx            mind_TradingAgent
   (import)           (读 reports/)      (读 JSON日志 + import)
```

注意：**三个子系统之间互不依赖**。它们各自独立运行，各自维护自己的数据源。融合引擎是唯一的集成点。
