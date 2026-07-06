# 当前项目状态快照

> 最后更新: 2026-07-02 (周四)
> 范围: 代码架构 + 运行时状态 + 关键配置 + 近期变更 + 待办
> 覆盖: src/、scripts/、services/、config/systemd/、docs/

---

## 一、系统全景

Aistock_vnpy_Trading 是三系统信号融合决策平台（无执行层，输出仅为决策信号和仓位建议）。

**重要区分：实时 vs 准实时**

系统中有两种"实时"能力，不要混淆：

| 层级 | 触发方式 | 延迟 | 推送内容 | 组件 |
|------|---------|------|---------|------|
| **ML实时预警** | WebSocket事件驱动 | 秒级 | 个股ATR止损/均线突破/量价异动 | monitor.service → realtime_monitor.py |
| **Fusion准实时融合** | 文件轮询(300s) | 分钟级 | 三系统融合综合得分变化 | realtime-fusion.service → realtime_fusion.py |

ML实时预警是真正的**事件驱动的实时**：行情一跳就检查是否有止损触发、均线突破、量价异动，有则立即推送。Fusion准实时融合是**轮询驱动的近实时**：每5分钟扫一次文件交换区的三个json文件，计算融合得分变化，超过阈值才推送。两者各自独立运行，解决不同问题。

**准实时融合的价值评估**

文件交换区三信号的更新频率：
- ly_signal.json: 每日一次 (15:15) — RF模型预测T+1，盘中固定
- ml_signal.json: 每5分钟 — 因子层读DB计算，有新数据就变
- at_signal.json: 每日两次 (09:31/13:00) — LLM辩论跑完固定

盘中真正频繁变化的只有ml因子信号。准实时融合的价值不在于"更快发现行情变化"（ML实时预警已做到），而在于**把ml因子信号放到三系统坐标系中做上下文解读**：
1. 共识漂移监测：ml因子变化在ly已是+2的背景下只是确认；在ly为-1.5时则是分歧加剧
2. 分歧跟踪：ML预警看不到系统方向矛盾，准实时融合能看到"ly看空、ml转多、at中立"
3. 仓位建议联动：ML预警说"止损触发了"，准实时融合说"综合得分从+1.2降到-0.3"

**局限**：信号源变化不频繁，经常5分钟扫描无变化就跳过。定位应该是"盘中融合坐标系下的共识漂移监测"，不是"更快发现行情"。

### 1.1 三个子系统

| 缩写 | 系统 | 方法 | 频率 | 核心输出 |
|------|------|------|------|---------|
| ly | lynx_vnpy | RF+LGB双模型 + 15TA + 58Alpha158因子 | 日频 15:15 | 上涨概率 + L7信号 |
| ml | MindLynx-Aistock | 12因子 + 15策略 + LLM推理 | 日频/实时 | 综合评分 0-100 + 文本解释(纯展示) |
| at | mind_TradingAgent | 多智能体辩论 (LangGraph) | 09:31/13:00 | 5级评级 (Buy~Sell) |

### 1.2 两条融合路径

```
                 日终融合 (19:00)                        准实时融合 (09:33+)
                 ───────────────                        ────────────────
                 run_daily.py                           realtime_fusion.py
                 (oneshot, systemd timer)               (daemon, 每300s扫描)
                      │                                       │
                      ▼                                       ▼
     ┌──────────────────────────────────────┐    ┌──────────────────────────┐
     │ data_loader.py 零侵入读取三系统输出     │    │ data/realtime/ 文件交换区  │
     │ ├─ LynxDataLoader: import lynx_signal │    │ ├─ ly_signal.json (T+1)  │
     │ ├─ MindLynx: 读 reports/ 报告文件      │    │ ├─ ml_signal.json (因子) │
     │ └─ TradingAgent: 读 logs/ JSON        │    │ └─ at_signal.json (辩论) │
     └──────────────────────────────────────┘    └──────────┬───────────────┘
                      │                                       │
                      ▼                                       ▼
     ┌──────────────────────────────────────┐    ┌──────────────────────────┐
      │ fusion_engine.py                     │    │ realtime_fusion.py       │
      │ ├─ normalizer 归一化 (L7 映射)        │    │ ├─ 读文件交换区三json    │
      │ ├─ 分歧检测 + ML少数方增强            │    │ ├─ 加权融合 (同权重)     │
      │ ├─ 缺失系统权重重分配                 │    │ ├─ 分歧检测 + 标记      │
      │ └─ 决策映射 → 仓位建议                │    │ └─ 变化超阈值才推送     │
     └──────────────────────────────────────┘    └──────────────────────────┘
                      │                                       │
                      ▼                                       ▼
     ┌──────────────────────────────────────┐    ┌──────────────────────────┐
     │ wecom_notifier.py 企业微信推送         │    │ wecom_notifier.py        │
     │ (Markdown, L7分组, 三层结构)           │    │ (Markdown, 仅变化推送)   │
     └──────────────────────────────────────┘    └──────────────────────────┘
```

### 1.3 ml 因子层独立服务

```
     ml_factor_service.py (daemon, 每300s)
     ─────────────────────────────────────
     读取 stock_daily DB → FactorEngine 计算 12 因子
     → 横截面归一化 → tanh映射到L7 → 写 ml_signal.json
     完全绕过 LLM 层，纯数学计算。
```

---

## 二、运行时状态

### 2.1 systemd 服务

| 服务 | Type | 内存 | 当前状态 | 负责 |
|------|------|------|---------|------|
| scheduler | daemon | ~75MB | ✅ running | ML内部调度(10个定时任务) |
| monitor | daemon | ~13MB | ✅ running | WebSocket盘中监控 |
| ml-factor | daemon | ~15MB | ✅ running | 因子层纯数学计算(300s) |
| realtime-fusion | daemon | ~15MB | ⏸️ inactive(周末跳过) | 文件交换区扫描(300s) |
| fusion | oneshot | - | inactive(等待19:00) | 日终融合 |
| lynx-signal | oneshot | - | inactive(等待15:15) | 量化信号+推送 |
| TA | oneshot | - | inactive(等待09:31) | TradingAgent辩论 |
| calibrate-alphas | oneshot | - | ✅ timer 12:30 | Alpha权重自动校准 |
| diagnose-agreement | oneshot | - | ✅ timer 20:30 | LY+ML同向诊断数据积累 |
| eastmoney-rating | oneshot | - | ✅ timer 09:53 | 东方财富数据获取+简讯推送（09:53仅一次, 13:53已取消） |
| retrain-lgb | oneshot | - | ✅ timer 15:20 | LGB+RF模型自动重训(≥7天触发) |
| c1test-daily | oneshot | - | ✅ timer 20:00 | 🆕 统一回测快速模式 |
| c1test-weekly | oneshot | - | ✅ timer 周日10:30 | 🆕 统一回测全面模式 |

**常驻内存合计**: ~245+95+32 = **372MB**（0.6% of 62GB 总内存）

> 注意：monitor 245MB 主要来自 litellm+httpx 库加载，非内存泄漏。
> 基线值 2026-06-24：ml_factor=32MB, scheduler=95MB, monitor=245MB。
> 如 monitor 长期运行后超过 400MB 需排查。

### 2.2 timer 触发时间线

```
08:30 ─ scheduler ─── 日间情报(盘前)推送
10:00 ─ scheduler ─── 整点全量分析
11:00 ─ scheduler ─── 整点全量分析
11:45 ─ scheduler ─── 大盘复盘(文字+PDF)
12:30 ─ calibrate-alphas.timer ── 🆕 Alpha权重自动校准
13:00 ─ TA.timer ──── 第二轮TA深度分析
14:00 ─ scheduler ─── 整点全量分析
15:00 ─ scheduler ─── 整点全量分析
15:15 ─ lynx-signal ─ 量化信号建模+推送
15:20 ─ retrain-lgb.timer ── 🆕 LGB 模型自动重训
15:45 ─ scheduler ─── 大盘复盘(文字+PDF)

# 准实时融合 09:33~15:00 每5分钟扫描(仅工作日)
# 因子计算 持续每5分钟扫描(仅工作日)

19:00 ─ fusion.timer ── 日终融合+龙虎榜+评级PDF
20:30 ─ diagnose-agreement.timer ── 🆕 LY+ML同向诊断
Sun 20:00 ─ scheduler ─ 周末情报推送
Mon 07:30 ─ scheduler ─ 周末情报补量
```

### 2.3 文件交换区

```
data/realtime/
├── ly_signal.json    ← lynx_signal.py (write_ly_signal.py) 写入
├── ml_signal.json    ← ml_factor_service.py 写入 (因子层，非LLM)
├── at_signal.json    ← mind_TradingAgent 写入
└── alpha158_signal.json ← alpha158 daemon 写入（LGB+58因子）
```

---

## 三、关键配置

### 3.1 权重 (settings.yaml) — 2026-06-24 AT价值评估更新

| 系统 | 权重 | 说明 |
|------|------|------|
| mindlynx | **0.50** | 62.2% (p=0.010) 唯一统计显著的系统 |
| ly (lynx_vnpy) | **0.37** | 54.0% (p=0.373) 正向但不显著, RF+LGB+alpha158 |
| at (TradingAgent) | **0.00** | 48.2% (p=0.745) 纯随机, 归零累积数据待后续开发优化 |

**修正记录 (2026-06-24)**:
1. AT权重 0.05→0.00: bt_predictions 85样本评估, 48.2%准确率 p=0.745(不显著)。
   Fusion在有AT参与时准确率从57.6%降至51.4%, AT为纯噪音。
   系统继续运行积累数据, 待后续系统性改造后再评估。
2. LY 0.36→0.37, ML 0.48→0.50: AT移除后按比例重分配。

**历史修正 (7358ce8, 2026-06-18)**:
1. AT权重 0.10→0.05: 回测150样本AT 47.0%(31/66), z=-0.49不显著
2. `ml_factor`死配置移除: 权重定义在settings.yaml但`_compute_adjusted_weights`的weight_map只有3个系统
3. 分歧惩罚移除: `fusion_score -= penalty`改为`disagreement_capped`置信度标记
4. ML融合偏向 sentiment_score 80/20

fusion_mode: "dual"（同时输出linear+bayesian，CSV暴露linear层字段）

### 3.2 ML阈值校准 (2026-06-07)

| 参数 | 旧值 | 新值 | 效果 |
|------|------|------|------|
| sentiment_threshold.bull | 60 | 52 | 准确率74.6%, 覆盖率98% |
| sentiment_threshold.bear | 40 | 49 | balanced score 73.2 vs旧42.0 |

Flat zone (41-59): LLM方向信号弱(1.8% acc), 整体系数乘0.5。

### 3.3 贝叶斯融合参数 (reliability.py)

| 系统 | base_alpha | default_h | 说明 |
|------|-----------|----------|------|
| ly | 0.75 | 0.0 | sklearn predict_proba, 100%可重复 |
| ml | 0.55~0.65 | 0.15 | ~40%因子+~60%LLM, per-stock差异 |
| at | 0.25 | 0.25 | 纯LLM角色扮演, prompt敏感 |

**per-stock alpha覆盖**: 000592=0.8, 300652=0.3, 600372=0.8, 603189=0.8, 603557=0.3, 605368=0.4, 688202=0.3。其余用BASE_ALPHA默认值0.65。DB动态alpha优先级高于静态覆盖。

**数学否决权**: |P_ly - 0.50| > 0.30 时触发，根据其他系统方向决定覆盖强度 (0.4~0.8)。

### 3.4 L7 7级决策映射 (v3.1, emoji 2026-06-23 统一方向符号)

| L7 | 得分范围 | 信号 | 仓位建议 | emoji |
|----|---------|------|:--------:|:-----:|
| +3 | [2.5, 3.0] | 强烈看多 | 2-3成 | 🚀 |
| +2 | [1.5, 2.5) | 看多 | 1-2成 | 📈 |
| +1 | [0.5, 1.5) | 谨慎看多 | 0.5-1成 | ↗️ |
| 0 | (-0.5, 0.5) | 中性/持有 | 0成 | ➡️ |
| -1 | (-1.5, -0.5] | 谨慎看空 | 减仓至0.5成以内 | ↘️ |
| -2 | (-2.5, -1.5] | 看空 | 大幅减仓 | 📉 |
| -3 | [-3.0, -2.5] | 强烈看空 | 清仓 | 🚨 |

**v4.0 精度校准映射 (2026-06-29)**: ml 的 `normalize_mindlynx_score` 从 3 值对称映射升级为基于 598 样本回测精度的 7 值非对称映射。详见 `docs/decisions/accuracy-calibrated-mapping.md`。

### 3.5 ly 概率映射 (分段线性)

```
prob_up  0% → L7 -3.00   (钳位下限)
prob_up 25% → L7 -2.06   (S6 看空)
prob_up 35% → L7 -1.13   (S5 谨慎看空)
prob_up 45% → L7  0.00   (S4 中性下界)
prob_up 55% → L7  0.00   (S4 中性上界, flat zone)
prob_up 65% → L7 +2.06   (S2 看多)
prob_up 75% → L7 +3.00   (S1 强烈看多)
prob_up 100%→ L7 +3.00   (钳位上限)
```

---

## 四、推送架构

### 4.1 两套引擎对比

| 引擎 | venv | 类 | 能力 | 负责推送 |
|------|------|-----|------|---------|
| Fusion Engine | .venv (Python 3.10) | WeComNotifier | Markdown | 融合决策、准实时速报、量化信号 |
| MindLynx | systems/.../.venv (Python 3.12) | WechatSender | Markdown/Text/Image/File | 个股分析、大盘复盘PDF、评级PDF |

### 4.2 推送消息类型

| 类型 | 引擎 | 时间 | 说明 |
|------|------|------|------|
| 融合决策 | Fusion | 19:00 | L7分组，三层结构 |
| 准实时速报 | Fusion | 09:33+每5min | 仅变化超阈值推送 |
| 量化信号 | Fusion | 15:15 | 单行每只 |
| 大盘复盘 | MindLynx | 11:45/15:45 | 文字摘要+PDF |
| 个股分析 | MindLynx | 整点(10/11/14/15) | Markdown仪表盘 |
| 盘中告警 | MindLynx | 盘中触发 | ATR止损/均线突破 |
| 周末要闻 | MindLynx | 周日20:00 | 按股分组 |

### 4.3 最新推送格式 (2026-06-07 重构)

三层结构：
1. 一句话总结: 今日立场+看多/空分布+建议总仓位
2. 风险提醒: 止损预警+系统分歧+降级合并为统一⚠区块
3. 信号分组: 按L7等级分组展示，每只带三系统得分+仓位+止损

(wecom_notifier.py v3.0, commit 5734845)

### 4.4 推送配置

webhook 在项目根 `.env` 单点维护（`WECOM_WEBHOOK_URL`）。更换只需改一个文件。

---

## 五、回测系统

### 5.1 基础架构

```
fusion_engine.py → fusion_{date}.csv
    ↓
backtest.py record → bt_predictions 表 (SQLite)
    ↓
backtest.py check → unified_cache.ohlcv_cache.db (T+1行情)
    ↓
backtest.py report → 准确率报告
```

DB: `data/backtest/bt_results.db`，60列schema覆盖子系统有效性、ML dashboard字段。
当前数据量: 60条预测(6交易日×10股)，40条已匹配行情。

### 5.2 子命令

| 命令 | 功能 |
|------|------|
| init | 初始化回测DB |
| record | 从fusion CSV写入预测 |
| check | 匹配次日行情 |
| update | record+check (每日一次) |
| report | 累计报告 |
| report --detail | 含个股明细 |
| backfill | 扫描历史CSV回填 |
| simulate | 融合模拟交易 |
| walkforward | 滑动窗口验证 (train=20, test=10, step=5) |
| weight_sweep | 网格扫描权重组合 |

### 5.3 LY 性能演进

**2026-07-02 c1test 全量回测** (full模式):

| 系统 | 准确率 | 样本 | vs 6/29 |
|------|:-----:|:----:|:-------:|
| 融合 | **55.3%** | 246 | -2.4% |
| LY OOS (walk-forward) | **49.4%** | 682 | +0.4% |
| ML sentiment | **66.3%** | 938 | -1.4% |
| ML operation_advice | **27.5%** | 1004 | 0.0% |
| AT | **54.8%** | 62 | 0.0% |

> 📌 融合准确率下降主要受近期市场波动影响（7/1=37.5%, 6/30=45.5%）。ML 语义差距 39%——op_advice 修复(6deb8a2)未改善文本准确率。

**2026-06-28 — IC加权双模型集成**: 等权→IC比例(LGB 0.91/RF 0.09)，方向准确率 53.5%→**65.3%**，Pearson IC 0.144→**0.218**。模型产物已更新。(1dc785f)

**历史 WalkForward 结果** (OOS 46.7%, IS 88.9%) 使用的是旧等权模型。IC加权后的新 OOS 待积累足够窗口数据重新评估。

**数据管道**: 回测匹配行情已从单源改为3层fallback — data_warehouse(5源)→unified_cache→stock_analysis.db，解决akshare失败时跳过的数据空洞问题。(baf4921)

### 5.4 因子研究核心发现 (docs/research/factor-research-report.md)

| 结论 | 数据 |
|------|------|
| ICIR权重组合优于静态权重 | 日均IC 0.0340 vs 0.0035 |
| 最佳因子 | size_factor (IC=+5.58%, ICIR=+0.17) |
| 最差因子 | volume_trend (IC=-3.62%, ICIR=-0.12) |
| 中性化效果有限 | 12因子中仅2个改善，3个恶化 |

---

## 六、代码模块职责速查

### 6.1 src/ (Fusion venv)

| 文件 | 行数 | 职责 |
|------|:----:|------|
| fusion_engine.py | 825 | 融合核心：linear/bayesian/dual 三种模式 + 分歧ML少数方增强 |
| normalizer.py | 447 | L7映射 v3.1 + v4.0精度校准 + 概率空间工具 |
| reliability.py | 291 | 贝叶斯α/c/h参数 + 幻觉检测 + 置信度校准 |
| data_loader.py | 1056 | 零侵入三系统读取 + UnifiedCache集成 |
| realtime_fusion.py | 414 | 文件交换区扫描daemon |
| wecom_notifier.py | 261 | 企业微信推送 v3.0 |
| feature_bridge.py | 174 | 可选功能：龙虎榜/东方财富评级 |

### 6.2 services/ (Fusion venv)

| 文件 | 行数 | 职责 |
|------|:----:|------|
| ml_factor_service.py | 204 | 因子层纯数学计算(无LLM)，写ml_signal.json |
| market_data_fallback.py | 375 | 回测多源数据fallback链 (🆕) |

### 6.3 scripts/

| 文件 | 行数 | 职责 |
|------|:----:|------|
| run_daily.py | 582 | 日终融合入口 |
| backtest.py | 1391 | 完整回测框架 + 多源fallback |
| c1test.py | ~340 | 统一回测编排器 (🆕) |

---

## 七、主要变更记录

按时间顺序:

**6/3** — 基础建设
- realtime_fusion集成测试全覆盖 (84cc3c1)
- requirements.txt补全8个缺失依赖 (609f50d)

**6/5** — 推送重构
- 推送格式统一：大盘复盘+自选股+龙虎榜+雪球+量比+融合图标 (0b48af0)

**6/6** — 密集功能期

| 变更 | 描述 | 重要性 |
|------|------|--------|
| 推送架构重构 | 推送统一+文档化 (477756f) | 架构级 |
| c1skill audit 6bug修复 | 含ATR层级冷却独立计时 (41c56a2) | 运行安全 |
| ATR止损冷却修复 | 各倍率独立计时 (a611ea4) | bugfix |
| 回测DB扩展 | 子系统有效性跟踪 (7857b43) | 回测基建 |
| ML数据接入融合 | factor_z_score + trend_score + risk_alerts (9756f0c/ffeca81) | 信号路径 |
| ly独立回测 | --backtest标志 (06373c7) | 回测基建 |
| ly WalkForward | OOS=46.7% (f051f79) | 验证 |
| 融合模拟交易 | backtest.py simulate (5e6492b) | 回测基建 |
| operation_advice对齐 | 与sentiment_score提示词对齐 (bf04a70) | bugfix |
| ML子系统校准 | 60/40→52/49阈值, per-stock alpha, 提示词calibration (c6aa739) | **最重要** |
| 路线图文档 | ml_roadway/ml_backtest/ml_prompt (c6aa739) | 文档 |

**6/7** — ly能力启动期 (12 commits)

| 变更 | 描述 | commit |
|------|------|--------|
| 止损专项改进 | trailing_high上移止损 + 推送展示 + LLM校验 | 3b6f695 |
| c1skill建议 | WalkForward/去极值/参数敏感性/P0 bug/审核模式/LLM脱敏 | ad4aefa |
| 因子研究 | 中性化+ICIR衰减对比 | 9af52e7 |
| 融合推送重组 | 一句话总结+风险聚合区块 | 5734845 |
| vnpy依赖安装+数据桥接 | polars/lightgbm/plotly/talib, DB→Parquet | 0b7f83d |
| Pipeline管线 | Alpha158计算+LGB训练+IC分析 | 0b7f83d |
| Alpha158+LGB生产集成 | lynx_signal.py --alpha, 58因子predictor | 7f44621 |
| **Alpha158特征数修复** | alpha_predictor.py:180 dropna→fillna(0), 57→58特征对齐 | 83310bc |
| 双模型集成(默认) | RF+LGB并行, predict_ensemble() | 36dca84 |
| ml_factor融合接入 | 12因子15% blend进ly | f4b09da |
| vnpy独立回测 | BacktestingEngine集成 | 890f7b6 |
| alpha158独立服务 | 58因子+LGB每5分钟, 10% blend | 0a01127 |
| ml_factor清理 | 移除冗余blend, alpha158为唯一ly增强 | d2ffdc9 |
| 架构文档更新 | D8决策, ly全景更新 | 当前 |

**关键架构决策**: 12因子(mL)和58Alpha158因子(ly)是两套互补独立的因子体系,不是超集子集关系。
ml的12因子有独特信号(illiquidity/max_effect/volume_trend等),能覆盖58因子没有的维度。
ly的58因子提供体系化覆盖(K线形态/多窗口统计/分位数等)。
两者互补独立,通过融合引擎各自投票——这是三系统融合架构的核心优势。

---

### 6/29 — c1test里程碑 + v4.0精度校准 + 分歧修复

| 变更 | 描述 | commit |
|------|------|--------|
| c1test统一回测上线 | 编排器+PHASE1~4+变化检测+统一报告 | a065866 |
| v4.0精度校准映射 | 3值对称→7值非对称(598样本回测) | 54136eb |
| 分歧→ML少数方增强 | 分歧时ML自适应提分(+0~0.3) | ccc8ed0 |
| LLM大数值幻觉修复 | 5处prompt注入数值上限保护 | fb72ad1 |
| LLM注入数据缺失单位 | 补全prompt中单位信息 | a2f7be1 |
| AT提示词重写 | 美股→A股适配 + 5个新模块 | a2f7be1 |

### 6/30 — ML 语义差距终结 + 全系统审查 + 资金流修复

| 变更 | 描述 | commit |
|------|------|--------|
| op_advice完全退出L7裁决 | 纯文本解释器，不参与融合打分 | 42c01fd |
| ML-LLM 3 Actions执行 | 因子剖面2行→12行、prompt重排、方向守卫 | 6deb8a2 |
| 全系统17项修复 | P0×2 P1×7 P2×8，覆盖13文件 | 27749fe |
| 资金流A'+B'修复 | tushare优先+akshare降级+数据湖缓存 | 0a6521c, a879e97 |
| 回测多源fallback | warehouse→cache→analysis_db三层降级 | baf4921 |

### 7/1 — 资金流补丁 + 全系统推送精细化

| 变更 | 描述 | commit |
|------|------|--------|
| 资金流数据链补丁 | _safe_float清洗/akshare参数名/hasattr保护 | 3efd985 |
| PDF关注度bug | min(len,1)→max(len,1)修复 | 60ae2f7 |
| 东方财富int(float()) | 兼容浮点数字符串崩溃 | cdf0e2e |
| 资金流单位感知 | _safe_float单位感知转换 | d886fe9 |
| 推送引擎标识移除 | 全系统移除lma/ly/ml引擎标记 | 7817275 |
| 融合决策精细化 | 移除emoji空格/仓位前缀/名价分隔符 | b02c138 |
| 中性涨跌幅bug修复 | pct变量冲突修复 | e09f232 |

### 7/3 — LY 深度分析 + Alpha158 横截面验证 + 移除 ML 中 LY 注入

| 变更 | 描述 | 涉及文件 |
|------|------|---------|
| LY 因子工程与模型架构文档 | 完整报告存至 `docs/subsystems/ly/architecture.md`，含 15TA+Alpha158+c1skill 分析 | 新增文档 |
| Alpha158 横截面 IC 验证 | 脚本验证横截面 |IC|=0.0158 不优于时序 |IC|=0.0321，结论：信号天花板非方法论问题 | `scripts/research_alpha158_cross_section.py` |
| 取消 LY _sign 阈值 0.1→0 | LY 样本 161→257，口径统一 | `scripts/backtest.py` |
| **移除 ML prompt 中 LY 信号注入** | LY OOS 50.1% 注入 ML 64% 系统是噪音，暂注释等待 c1test 验证 | `analyzer.py`, `executor.py` |

## 八、待办与优先级

### 2026-07-03 — 当前状态总览

| 薄弱环节 | 状态 | 最新进展 |
|---------|:----:|---------|
| ML 40pp语义差距 | **✅ 已关闭** | op_advice完全退出L7裁决, 纯文本解释器 |
| v4.0精度校准映射 | **✅ 已部署** | 7值非对称映射, normalizer.py |
| 分歧惩罚→ML少数方增强 | **✅ 已部署** | 分歧时ML自适应提分+0~0.3 |
| 全系统代码审查 | **✅ 已完成** | 17项全部修复 |
| 资金流数据缺失 | **✅ 已修复** | 3轮补丁: tushare优先+多API fallback+数据湖 |
| AT美股→A股适配 | **✅ 已修复** | 提示词重写, 权重0.00积累数据 |
| LY IC加权 | **✅ 已部署** | 方向准确率53.5%→65.3% |
| LY 信号天花板 | **✅ 确认** | 横截面 IC 验证不优于时序, 经典因子在 A 股个股上信号弱 |
| LY 注入 ML prompt | **⏳ 已暂注释** | 等待 c1test 对比验证后决定是否永久移除 |
| op_advice文本准确率27.5% | **⚠️ 修复未生效** | 3Actions 未改善, 等待决策 |
| 融合基线55.3% | **⏳ 待观察** | 持续跟踪 |

### 当前待办

| 优先级 | 任务 | 说明 |
|:------:|------|------|
| P1 | **c1test 对比 LY 注入移除效果** | 注释后跑几天数据, 对比 ML sentiment 准确率变化 |
| P1 | LY OOS重新评估 | IC加权双模型后walk-forward验证 |
| P2 | backtest --force 重算 | 用52/49阈值+HP3双路径重新评估全部历史记录 |
| P3 | AT数据积累 | 新prompt持续运行积累forward数据 |

### ✅ 已完成修复 — c1test 数据链路 (2026-07-02)

| 变更 | 说明 |
|------|------|
| LY walkforward 持久化到 bt_meta | c1test full 模式自动记录, 支持 OOS 趋势追踪 |
| 子系统覆盖率加入报告 | JSON+MD 双输出, 覆盖率<50%自动告警 |

**严重级别 (8项)**:
- S1 `data_loader.py:336` — for缩进错误修复，ML数据从仅1只变为全量10只
- S2 `calibrate_alphas.py:121` — 原子写入，崩溃不丢文件
- S8 `normalizer.py:316+334` — map_normalized_to_label/score_to_l7_integer阈值对齐L7_THRESHOLDS（旧0.5→1.0）
- S3 `retrain_lgb.py:55` — SQL f-string改为参数化查询
- H1 `data_loader.py:1027,1029` — bool强制转换if x→is not None，score=0不再被吞
- H2 4处except:pass → logger.warning
- H3 `realtime_fusion.py:101` — 文件句柄泄露修复
- H6 `run_daily.py:532` — 子进程加capture_output+返回码检查

**功能修复 (7项)**:
- H4 `fusion_engine.py:312` — 贝叶斯分歧检测加p_ml（之前只检查ly/at）
- H8 `config/settings.yaml` — at权重0.30→0.25与生产一致
- S4 `lynx_signal.py:580` — 回测前视偏差修复（删训练集最后一行）
- S7 `trading_graph.py` — TA图执行加3600s超时保护
- M2 `wecom_notifier.py:289` — list.index()→enumerate()
- F2 `wecom_notifier.py:167` — format_daily_summary date参数生效
- M5 `fusion_engine.py:703` — get_portfolio_summary加.get()保护

**代码质量 (6项)**:
- M6 import移出for循环
- L3 删未用import
- L4 run_daily.py `in dir()` → 常规写法
- M12 6文件清理未用import
- H7 `expr_cache.py` 异常加日志
- H9 `stock_knowledge.py` with语句防连接泄露
- M10 analyzer JSON校验加3层回退
- M3 data_loader.py删重复键

### P2-c1skill 之前已实现的改进

✅ WalkForward融合回测 (train=20, test=10, step=5)
✅ MAD去极值 (factor_engine.py winsorize_mad, 阈值5.0≈3.35σ)
✅ 参数敏感性测试 (cmd_weight_sweep 网格扫描)
✅ ml/at valid默认值False + 防御性归一化守卫
✅ --review-mode + --confirm 审核模式
✅ LLM prompt脱敏 (prompt_anonymize配置)
✅ 回测报告增强 (盈亏比+最大连续亏损)

---

## 九、已知陷阱（运维注意）

### 9.1 双副本同步

`systems/MindLynx-Aistock/` 是独立文件副本（非子模块/符号链接）。
修改MindLynx源码后，必须手动cp到副本目录并重启相关进程。
受影响: scheduler, monitor (均运行在副本路径下)。

同步前检查 `git log --oneline --follow <file>` 确认副本是否有本地独有优化。

### 9.2 推送格式陷阱

- `--single-notify` 模式走不同代码路径 (format1不是format2)，修改 `generate_wechat_dashboard()` 后需验证日志含 `[format2]` 标记。
- LLM返回数据可能含markdown语法，`_clean_md()` 需在截断前调用。
- ideal_buy截断陷阱: 先清洗再截断，factor_summary截断从80字改为120字。

### 9.3 周末空转

realtime-fusion和ml-factor服务已加入 `_is_trading_day()` 检测，非工作日睡眠到下一交易日09:33。

### 9.4 前视偏差陷阱（已修复）

`lynx_signal.py` 回测中 `compute_features()` 在全量数据集上计算 target（`shift(-1)`），再用 `df.iloc[:train_end]` 训练。最后一行 target 用了测试集收盘价。**已修复**: 训练改为 `df.iloc[:train_end - 1]`。

### 9.5 连接泄露陷阱（已修复）

`stock_knowledge.py` 中 `sqlite3.connect()` 在函数头打开、140行后才关闭。中间5+异常路径可能跳过close。**已修复**: 改为 `with sqlite3.connect() as conn` 自动释放。

---

## 十、数据源与依赖

| 源 | 优先级 | 用途 | 稳定性 |
|----|--------|------|--------|
| Tushare Pro | 最高 | 实时行情/OHLCV/资金流/估值 | 稳定(已付费) |
| Sina API (hq.sinajs.cn) | 高 | 实时行情后备 | 稳定 |
| Tencent (akshare) | 中 | PE/PB/量比/换手率补充 | 稳定 |
| EastMoney/efinance | 中 | 龙虎榜/基本面/板块排行 | 不稳定，常被封 |
| akshare (EM/Sina) | 后备 | 补充数据 | 版本依赖敏感 |
| pytdx (TCP) | 后备 | 日K线降级 | 稳定 |

**回测数据fallback链** (baf4921, 2026-06-30): `data_warehouse(5源) → unified_cache → stock_analysis.db` — 3层降级保障回测行情匹配不停摆。详见 `src/market_data_fallback.py`。

---

## 十一、数据湖改造计划（方案 B，待执行）

### 11.1 背景

当前实时行情/估值数据的获取模式是"LLM 分析时实时调用 API"。
当 efinance 被封锁或响应慢时，LLM 分析的数据完整性下降。

### 11.2 估值数据缺失根因（2026-07-06 已定位+修复）

```
两条调用路径, 不同超时:
  主流程: get_realtime_quote() → 超时 ~15s → Tushare OK + Tencent 补充 ✅
  估值入库: get_realtime_quote() → 超时 3s → Tushare 刚返回就超时 ❌
```

**修复**: 
- `FUNDAMENTAL_FETCH_TIMEOUT_SECONDS=20`（3→20s）
- **数据湖兜底**: `get_fundamental_context()` 优先检查 `WarehouseReader`，
  API 成功时回写 `DataLake`，API 失败时使用湖中旧数据（哪怕过期）。
  确保整点分析始终能拿到估值数据，而非 None。

### 11.3 方案 B：数据湖定时拉取

利用已有的 `data_warehouse` 服务，定时后台拉取不敏感数据：

| 数据 | 更新频率 | 时效性 | 当前问题 |
|------|:--------:|:------:|---------|
| 估值(PE/PB) | 日频 | 次日有效 | API 超时 3s（已修复至 10s） |
| 筹码分布 | 日频 | 次日有效 | 已启用（Tushare Pro） |
| 资金流 | 15分钟 | 盘中有效 | 实时调用可能失败 |
| 板块排行 | 15分钟 | 盘中有效 | efinance 被封时丢失 |

### 11.4 预期收益

- 整点分析读取缓存而非实时 API，速度更快
- API 故障不影响已有数据分析
- 多个子系统共享缓存，减少 API 调用量

### 11.4 执行前提

- 需确定哪些数据适合缓存（日频数据优先）
- 需实现 data_warehouse 的定时更新 scheduler
- 需修改 pipeline.py 的数据读取路径（优先读湖，API 兜底）

> 待深入论证后执行。参见 `docs/data-chain/data-warehouse.md`。

---

## 十一、股票池

10只A股: 001390古麒绒材(SZ), 300652雷迪克(SZ), 600372中航机载(SH), 605368蓝天燃气(SH), 000592平潭发展(SZ), 603189*ST网达(SH), 603557*ST起步(SH), 688202美迪西(SH), 601801皖新传媒(SH), 300676华大基因(SZ)
