# 系统架构全景

> 生成日期: 2026-06-30
> 信息来源: `docs/architecture/current-state.md`, `config/settings.yaml`, `src/fusion_engine.py`,
> `src/data_loader.py`, `src/wecom_notifier.py`, `scripts/c1test.py`, `docs/push/format.md`,
> 运行时 `systemctl` 状态
> 验证方法: 逐项对照代码确认

---

## 一、整体定位

**三系统信号融合决策平台**，无交易执行层。输出仅为 L7 7 级决策信号和仓位建议，
通过企业微信推送给人做投资参考。平台本身不连接券商 API，不下单。

---

## 二、纵向四层架构

```
┌─────────────────────────────────────────────────────────┐
│                     🧠  LLM 分析层                       │
│  MindLynx-Aistock (因子+LLM推理)                        │
│  TradingAgent (多智能体辩论)                             │
│  整点分析 / 大盘复盘 / 情报搜集 / 盘中告警               │
│  [常驻daemon: scheduler (~75MB), monitor (~245MB)]      │
├─────────────────────────────────────────────────────────┤
│                   📊  因子计算层                         │
│  lynx_signal: RF+LGB 模型 (日频15:15)                   │
│  ml_factor_service: 12因子纯数学 (300s轮询)             │
│  Alpha158 daemon: 58因子+LGB (300s轮询)                │
│  [常驻daemon: ml-factor (~15MB), alpha158]              │
├─────────────────────────────────────────────────────────┤
│                  🔗  融合决策层                          │
│  日终融合 (fusion_engine.py, 19:00 oneshot)             │
│  准实时融合 (realtime_fusion.py, 09:33+每5min)          │
│  分歧检测 / 贝叶斯融合 / L7决策映射                     │
├─────────────────────────────────────────────────────────┤
│                 📢  推送通知层                           │
│  WeComNotifier (Fusion venv, Python 3.10) — Markdown    │
│  WechatSender (ML venv, Python 3.12) — MD/Text/File    │
│  13种推送类型 → 统一企微群                              │
└─────────────────────────────────────────────────────────┘
```

---

## 三、三个子系统

| 缩写 | 全称 | 方法 | 频率 | 融合权重 |
|:----:|------|------|:----:|:-------:|
| **ly** | lynx_vnpy | RF + LGB + 15技术指标 + Alpha158(58因子) | 日频 15:15 | **0.37** |
| **ml** | MindLynx-Aistock | 12因子 + 15策略 Agent + LLM推理 | 日频/实时 | **0.50** |
| **at** | mind_TradingAgent | 多智能体辩论 (LangGraph, DeepSeek) | 09:31/13:00 | **0.00** |

来源: `config/settings.yaml:weights` 区块。

### 3.1 ly — 量化信号子系统

- 模型: RandomForest + LightGBM 双模型集成 (`lynx_signal.py`)
- 因子: 15技术指标 (RSI/MACD/ATR/K线形态等) + Alpha158 (58因子)
- ML输出: 上涨概率 (0~100%) + L7 7级信号
- 增强: Alpha158 LGB 信号以 10% blend 增强 ly (run_daily.data_loader)
- 回测: Walk-Forward (train=20, test=10, step=5), OOS=49.0% (当前数据管道待修复)
- 验证: `src/data_loader.py` 通过 Python import 直接调用，零侵入

### 3.2 ml — AI分析子系统

- 因子引擎: 12因子 (illiquidity/max_effect/volume_trend 等), 纯数学计算 (`ml_factor_service.py`)
- LLM分析: 6个策略 Agent (50+提示词版本历史) + MarketAnalyzer
- 双路径输出: sentiment_score (评分 0-100) + operation_advice (操作建议文本)
- 融合路径: sentiment_score 占 80%, operation_advice 占 20% (`fusion_engine.py:533`)
- 数据源: `stock_analysis.db` (1201条历史, 2026-05-18 起)

### 3.3 at — 多智能体辩论子系统

- 架构: LangGraph 多智能体辩论 (Bull/Bear/Critic/Strategist)
- 模型: DeepSeek API (deepseek-chat)
- 输出: 5级评级 (Strong Buy ~ Strong Sell)
- 状态: 48.2% (p=0.745) 纯随机噪音 → 权重归零, 系统继续运行积累数据
- 注: 之前错用了美股提示词跑A股, 2026-06-29已重写适配

---

## 四、两条融合路径

### 4.1 日终融合 (19:00 oneshot)

```
run_daily.py → data_loader (零侵入读取三系统) → fusion_engine.py → wecom_notifier.py
```

- 数据来源:
  - ly: Python import `lynx_signal` 模块直接调用
  - ml: 读取 `reports/` 目录 Markdown 报告文件
  - at: 读取 `logs/` JSON 文件
- 零侵入原则：不修改子系统任何代码，通过文件/import 接口读取
- 融合模式: `fusion_mode: "dual"` — 同时输出 linear + bayesian 两种结果
- 分歧检测: 三系统方向不一致时标记 (`has_disagreement`)，分歧时 ML 准确率从 37.9%→51.0%
- 分歧惩罚: 已移除分数扣减，改为置信度标记 (Oracle 2026-06-18 验证)

### 4.2 准实时融合 (09:33+ 每5min daemon)

```
realtime_fusion.py → data/realtime/ 文件交换区 → wecom_notifier.py
```

- 文件交换区:
  - `ly_signal.json` — lynx_signal 写入 (T+1 预测, 盘中不变)
  - `ml_signal.json` — ml_factor_service 写入 (因子层, 每300s更新)
  - `at_signal.json` — TradingAgent 写入 (09:31/13:00)
- 仅融合得分变化超阈值时推送
- 盘中真正频繁变化的只有 ml 因子信号
- 价值: 把 ml 因子变化放到三系统坐标系中做上下文解读

### 4.3 三种融合模式 (fusion_engine.py)

| 模式 | 说明 | 当前状态 |
|------|------|---------|
| linear | 线性加权 (权重×信号求和) | ✅ 输出到 CSV |
| bayesian | 贝叶斯概率融合 (reliability.py α/c/h) | ✅ 输出到 CSV |
| dual | 同时输出两种 | **当前默认** `fusion_mode: "dual"` |

### 4.4 融合权重 (当前生产值)

| 系统 | 权重 | 依据 |
|------|:----:|------|
| mindlynx | **0.50** | 62.2% (p=0.010) 唯一统计显著系统 |
| lynx_vnpy | **0.37** | 54.0% (p=0.373) 正向但不显著 |
| tradingagent | **0.00** | 48.2% (p=0.745) 纯随机噪音, 归零 |

来源: `config/settings.yaml`

### 4.5 决策映射 (L7 7级, v3.1)

得分范围 [-3.0, +3.0] 映射为 7 级信号:

| L7 | 范围 | 信号 | 仓位 | emoji |
|:--:|------|------|:----:|:----:|
| +3 | [2.5, 3.0] | 强烈看多 | 25% | 🚀 |
| +2 | [1.5, 2.5) | 看多 | 15% | 📈 |
| +1 | [0.5, 1.5) | 谨慎看多 | 7.5% | ↗️ |
| 0 | (-0.5, 0.5) | 中性/持有 | — | ➡️ |
| -1 | (-1.5, -0.5] | 谨慎看空 | 2.5% | ↘️ |
| -2 | (-2.5, -1.5] | 看空 | 1.5% | 📉 |
| -3 | [-3.0, -2.5] | 强烈看空 | — | 🚨 |

### 4.6 ML 精度校准映射 (v4.0, 2026-06-29)

`normalize_mindlynx_score` 从 3 值对称 → 7 值非对称映射 (基于 598 样本回测精度):

| Sentiment | L7 | 依据 |
|:---------:|:--:|:----:|
| 0-19 | -3.0 | 100.0% acc |
| 20-30 | -2.5 | 89.0% acc |
| 31-40 | -2.0 | 92.8% acc |
| 41-48 | -1.5 | 92.8% acc |
| 49-51 | 0.0 | 0.0% acc (flat zone) |
| 52-59 | +0.8 | 56.2% acc, 保守 |
| 60-79 | +1.0 | 38.2% acc, 阻尼 |
| ≥80 | +1.5 | extrapolated |

来源: `src/normalizer.py:260-284`, `docs/decisions/accuracy-calibrated-mapping.md`

### 4.7 贝叶斯融合参数 (reliability.py)

| 系统 | base_alpha | default_h |
|------|-----------|----------|
| ly | 0.75 | 0.0 |
| ml | 0.55~0.65 | 0.15 |
| at | 0.25 | 0.25 |

---

## 五、运行时服务

### 5.1 systemd 常驻 daemon

| 服务 | 类型 | 内存 | 状态 | 职责 |
|------|------|:----:|:----:|------|
| scheduler | 常驻 | ~75MB | ✅ running | ML内部调度 (整点分析/大盘复盘/情报) |
| monitor | 常驻 | ~245MB | ✅ running | WebSocket实时盘中监控 |
| ml-factor | 常驻 | ~15MB | ✅ running | 12因子纯数学计算 (300s轮询) |
| data-warehouse | 常驻 | — | ✅ running | 统一数据缓存+限流+调度 |
| realtime-fusion | 常驻 | ~15MB | ⏸️ inactive(非交易时段) | 文件交换区扫描 (300s) |

来源: `systemctl --user list-units`, `docs/architecture/current-state.md §2.1`

### 5.2 oneshot 定时器

| 定时器 | 时间 | 职责 |
|--------|:----:|------|
| fusion.timer | 19:00 | 日终融合+龙虎榜+评级PDF |
| lynx-signal.timer | 15:15 | 量化信号建模+推送 |
| TA.timer | 09:31/13:00 | TradingAgent 辩论 |
| eastmoney-rating.timer | 09:53/14:53 | 东方财富评级简讯推送 |
| calibrate-alphas.timer | 12:30 | Alpha权重自动校准 |
| c1test-daily.timer | 20:00 | 统一回测快速模式 |
| c1test-weekly.timer | 周日10:30 | 统一回测全面模式 |

### 5.3 内存合计

常驻: ~75+245+15 ≈ **335MB** (0.5% of 62GB)

---

## 六、推送层

### 6.1 两套推送引擎

| 引擎 | venv | Python | 推送类 | 能力 |
|------|------|:------:|--------|------|
| Fusion Engine | `.venv/` (项目根) | 3.10 | `WeComNotifier` | Markdown |
| MindLynx | `systems/.../venv/` | 3.12 | `WechatSender` | Markdown/Text/Image/File |

来源: `docs/push/format.md`, `src/wecom_notifier.py`, `systems/MindLynx-Aistock/src/notification_sender/wechat_sender.py`

### 6.2 13种推送类型

| # | 推送类型 | emoji | 时间 | 引擎 | 格式章节 |
|:-:|---------|:-----:|:----:|:----:|:--------:|
| 1 | 融合决策 | 🛟 | 19:00 | Fusion | §2.1 |
| 2 | 准实时速报 | 🛟 | 09:33+ | Fusion | §2.2 |
| 3 | 量化信号 | 🧬 | 15:15 | Fusion | §2.3 |
| 4 | 整点分析 | 👾 | 11:00/14:00 | ML | §2.4 |
| 5 | 每日情报 | 📰 | 08:30 | ML | §2.5 |
| 6 | 周末情报 | 📰 | 周日20:00 | ML | §2.5 |
| 7 | 大盘复盘 | 🎯 | 11:45/15:45 | ML | §2.6 |
| 8 | 盘中速报 | 👾 | 事件驱动 | ML | §2.7 |
| 9 | ATR止损 | 🚨 | 事件驱动 | ML | §2.7 |
| 10 | 异动预警 | 🔥 | 事件驱动 | ML | §2.7 |
| 11 | 均线突破 | 📈/📉 | 事件驱动 | ML | §2.7 |
| 12 | 评级PDF | 💰 | 19:01 | ML | §2.8 |
| 13 | 评级简讯 | 💰 | 09:53/14:53 | ML→Fusion | §2.9 |

来源: `docs/push/format.md §7`

---

## 七、c1test 统一回测

### 7.1 架构

```
c1test.py (编排器)
├── Phase 1: 融合回测 (子进程 backtest.py + 直查 bt_results.db)
├── Phase 2: LY独立回测 (子进程 lynx_signal.py --backtest)
├── Phase 3: ML独立回测 (直查 stock_analysis.db — 双路径)
├── Phase 4: AT独立回测 (TA JSON日志 + stock_daily T+1匹配)
├── 变化检测 (对比 last_run.json → 红黄绿告警)
└── 统一报告 (unified_report.json + .md)
```

来源: `scripts/c1test.py`

### 7.2 当前结果 (2026-06-29)

| 系统 | 准确率 | 样本 | 说明 |
|------|:-----:|:----:|------|
| 融合 | **57.7%** | 284 | 线性加权三系统 |
| ML sentiment_score | **67.7%** | — | 直查 analysis_history + T+1行情, 路径最优 |
| ML operation_advice | **27.5%** | 1602 | 文本路径, 仅作参考 |
| ML 融合方向 | **62.3%** | 175 | 融合系统中的 ML 贡献 |
| LY OOS | **49.0%** | 682 | Walk-forward (数据管道待修复) |
| AT 独立 | **54.8%** | 62 | 新prompt首日 |

---

## 八、数据流

```
外部数据源
  ├─ Sina API → 实时行情 (WebSocket → monitor daemon)
  ├─ EastMoney / akshare → 龙虎榜/东方财富评级
  ├─ Tushare Pro (付费) → 资金流数据补充
  └─ 新闻/RSS → 情报搜集 → LLM注入
        │
        ▼
  数据湖 (data_warehouse)
  ├─ stock_daily DB (因子引擎计算)
  ├─ stock_analysis.db (ML分析历史)
  ├─ bt_results.db (回测记录)
  ├─ eastmoney_rating.json (东方财富缓存)
  └─ unified_cache (共享OHLCV, TTL 24h)
        │
        ▼
  因子引擎
  ├─ lynx_signal: RF+LGB → ly_signal.json + 上涨概率
  ├─ ml_factor_service: 12因子 → ml_signal.json
  └─ Alpha158: 58因子+LGB → alpha158_signal.json
        │
        ▼
  三系统输出
  ├─ reports/ (ML分析报告)
  ├── data/realtime/*.json (文件交换区)
  └── logs/ (TradingAgent 辩论结果)
        │
        ▼
  融合引擎 → 决策信号 → 企业微信推送
```

---

## 九、关键架构决策

| 决策 | 内容 | 依据 |
|------|------|------|
| **零侵入** | 融合引擎不修改子系统代码 | 通过 import/文件/DB 接口读取 |
| **AT权重归零** | 0.00, 保留运行积累数据 | 48.2% p=0.745 纯随机噪音 |
| **精度校准 v4.0** | sentiment_score 3值→7值非对称 | 看空89-100% vs 看多38-56% |
| **分歧标记代替惩罚** | 分歧时不扣分, 只标记 | 分歧时ML 37.9%→51.0% |
| **ML 80/20偏向** | sentiment_score 80% + op_advice 20% | 67.7% vs 27.5% |
| **dual模式** | 同时输出 linear+bayesian | 便于对比验证 |
| **两套venv** | Fusion 3.10 + ML 3.12 | ML子系统独立演进 |

---

## 十、当前薄弱环节

| 问题 | 状态 | 影响 |
|------|:----:|------|
| LY OOS 49.0% | 数据管道待修复 (非架构问题) | ly权重价值待验证 |
| ML op_advice 27.5% vs sentiment 67.7% | 40pp语义差距, 已优化prompt | 不影响融合 (80/20偏向) |
| 资金流数据偶有缺失 | A'+B'修复已上线, 待验证 | 整点分析中量比/成交额字段 |
| 融合 57.7% | 基线值 | 需三个子系统均>60%才能突破65% |
| AT权重归零 | 等待系统性改造 | 不参与当前融合决策 |

---

## 附：验证对照表

| 声明 | 验证来源 | 结论 |
|------|---------|:----:|
| 权重 ml=0.50 ly=0.37 at=0.00 | `config/settings.yaml:weights` | ✅ |
| fusion_mode=dual | `config/settings.yaml:fusion_mode` | ✅ |
| 三种融合模式 (linear/bayesian/dual) | `src/fusion_engine.py` | ✅ |
| 零侵入读取三系统 | `src/data_loader.py` docstring + 代码 | ✅ |
| 常驻4个服务 | `systemctl --user list-units` | ✅ |
| 13种推送类型 | `docs/push/format.md §7` | ✅ |
| L7 7级映射 | `src/normalizer.py:L7_THRESHOLDS` | ✅ |
| c1test 四阶段 | `scripts/c1test.py` | ✅ |
| 精度校准 v4.0 | `src/normalizer.py:260-284` | ✅ |
