# Aistock_vnpy_Trading 系统架构文档

> 最后更新: 2026-06-06
> 基于完整三轮审计：推送配置统一 → 定时触发链分析 → c1skill 反方论证

---

## 一、系统全景

```
┌──────────────────────────────────────────────────────────────────┐
│                     Aistock_vnpy_Trading                          │
│              三系统融合决策平台 (MIT License)                       │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐       │
│  │  lynx_vnpy   │  │MindLynx-    │  │ mind_            │       │
│  │  (量化信号)   │  │Aistock      │  │ TradingAgent     │       │
│  │  ly          │  │(AI分析)     │  │ (多智能体辩论)    │       │
│  │              │  │ ml          │  │ at               │       │
│  ├──────────────┤  ├──────────────┤  ├──────────────────┤       │
│  │ RandomForest │  │ 12因子+策略  │  │ 多空辩论         │       │
│  │ 技术指标     │  │ LLM推理     │  │ 风险讨论         │       │
│  │ 上涨概率%    │  │ 评分0-100   │  │ 5级评级          │       │
│  └──────┬──────┘  └──────┬──────┘  └────────┬─────────┘       │
│         │                │                    │                 │
│         │    ┌───────────┴────────────┐       │                 │
│         │    │  data/realtime/ 文件交换区 │       │                 │
│         │    │  ly_signal.json        │       │                 │
│         │    │  ml_signal.json        │       │                 │
│         │    │  at_signal.json        │       │                 │
│         │    └───────────┬────────────┘       │                 │
│         │                │                    │                 │
│         ▼                ▼                    ▼                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                Fusion Engine (src/)                       │  │
│  │  ├─ data_loader.py   → 零侵入三系统数据读取               │  │
│  │  ├─ normalizer.py    → 信号归一化 + L7 决策空间映射        │  │
│  │  ├─ fusion_engine.py → 线性/贝叶斯融合 + 分歧检测         │  │
│  │  ├─ wecom_notifier.py→ 企业微信推送 (Markdown)             │  │
│  │  ├─ realtime_fusion.py→ 准实时文件交换区扫描               │  │
│  │  └─ logger.py        → CSV/JSON 持久化                    │  │
│  └──────────────────────────┬───────────────────────────────┘  │
│                             │                                  │
│                             ▼                                  │
│                    📊 融合决策 + 仓位建议                        │
│                    📱 企业微信推送                              │
└──────────────────────────────────────────────────────────────────┘
```

---

## 二、子系统职责与边界

| 子系统 | 方法 | 频率 | 核心输出 | 独立性 |
|--------|------|------|---------|--------|
| **ly** (lynx_vnpy) | RandomForest 量化模型 + 15 技术指标 | 日频 (15:15) | 上涨概率 + L7 信号 | 独立 venv, 独立推送 |
| **ml** (MindLynx-Aistock) | 因子+策略+LLM 推理 | 日频/实时 | 综合评分 0-100 | 独立 venv, 独立推送 |
| **at** (mind_TradingAgent) | 多智能体辩论 (LangGraph) | 盘后 (09:31/13:00) | 5 级评级 | 独立 venv |

### 核心原则

> **三子系统必须保持完整独立** — 不修改子系统代码、不引入跨系统依赖、各自的 venv 和配置各自管理。

Fusion Engine 通过两种方式集成：
1. **零侵入数据加载** (`src/data_loader.py`) — 读子系统输出文件，不调子系统 API
2. **文件交换区** (`data/realtime/`) — 准实时融合通过 JSON 文件交换信号

---

## 三、部署架构

### 3.1 运行时概览

```
┌─────────────────────────────────────────────────────────────────┐
│                        系统组成                                   │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Fusion Venv (.venv/ Python 3.10)                          │  │
│  │  ├── scripts/run_daily.py         (19:00 日终融合)          │  │
│  │  ├── src/realtime_fusion.py       (09:33+ 准实时 daemon)    │  │
│  │  ├── src/wecom_notifier.py        (Markdown 推送)            │  │
│  │  └── systems/lynx_vnpy/lynx_signal.py (15:15 量化信号)       │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  MindLynx Venv (systems/MindLynx-Aistock/.venv/ Python 3.12)│  │
│  │  ├── main.py --schedule               (常驻调度器 daemon)    │  │
│  │  ├── main.py --realtime-monitor-daemon (盘中监控 daemon)     │  │
│  │  ├── src/notification_sender/wechat_sender.py (文件/图片推送) │  │
│  │  └── scripts/generate_rating_report.py (评级PDF生成)         │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  共享组件                                                   │  │
│  │  ├── config/settings.yaml          → Fusion 配置            │  │
│  │  ├── .env                          → 企业微信 webhook 统一   │  │
│  │  ├── config/stock_pool.csv         → 10 只 A 股股票池       │  │
│  │  └── data/realtime/                → 文件交换区 (ly/ml/at)  │  │
│  └────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 systemd 服务清单

| 服务 | 类型 | Venv | 内存 | 周末运行 | 说明 |
|------|------|------|------|---------|------|
| `scheduler.service` | 常驻 daemon | MindLynx | ~75MB | ❌ 智能跳过 | 内部调度 10 个定时任务 |
| `monitor.service` | 常驻 daemon | MindLynx | ~13MB | ❌ 智能跳过 | WebSocket 盘中监控 |
| `ml-factor.service` | 常驻 daemon | Fusion | ~15MB | ⚠️ 运行但无操作 | 因子计算 daemon |
| `realtime-fusion.service` | 常驻 daemon | Fusion | ~15MB | ❌ 周末跳过 | 文件交换区扫描 (v2 新增) |
| `fusion.service` | oneshot | Fusion | - | ❌ 仅工作日 | 19:00 日终融合 |
| `lynx-signal.service` | oneshot | Fusion | - | ❌ 仅工作日 | 15:15 量化信号 |
| `TA.service` | oneshot | Fusion | - | ❌ 仅工作日 | 09:31/13:00 TA 分析 |

**总计常驻内存**: ~118MB（scheduler 75MB + monitor 13MB + ml-factor 15MB + realtime-fusion 15MB）

### 3.3 定时触发时间线（标准交易日）

```
08:30 ─ scheduler ─── 日间情报(盘前)推送
09:31 ─ TA.timer ──── mind_TradingAgent 深度分析 (LLM, ~30min)
09:33 ─ realtime-fusion.timer ─── 启动准实时 daemon (每300s扫描文件交换区)
10:00 ─ scheduler ─── 整点全量分析
11:00 ─ scheduler ─── 整点全量分析
11:45 ─ scheduler ─── 大盘复盘 (文字摘要 + PDF)
13:00 ─ TA.timer ──── 第二轮 TA 深度分析
14:00 ─ scheduler ─── 整点全量分析
15:00 ─ scheduler ─── 整点全量分析
15:15 ─ lynx-signal.timer ── 量化信号建模 + 推送
15:45 ─ scheduler ─── 大盘复盘 (文字摘要 + PDF)
19:00 ─ fusion.timer ──── 日终融合 + 龙虎榜 + 东方财富评级PDF
Sun 20:00 ─ scheduler ─ 周末情报推送
Mon 07:30 ─ scheduler ─ 周末情报补量
```

---

## 四、推送架构

### 4.1 统一配置

企业微信 webhook **只有一个维护点**：

```
.env (项目根)
├── WECOM_WEBHOOK_URL   → Fusion(wecom_notifier) + lynx_signal
└── WECHAT_WEBHOOK_URL  → MindLynx(wechat_sender) [别名, 同一值]
```

其他位置（`settings.yaml`、`MindLynx/.env`）已清空或引用根 `.env`。更换 webhook 只需改 `.env` 一个文件。

### 4.2 两套推送引擎（设计如此，不是缺陷）

| 引擎 | 所在 venv | 发送类 | 能力 | 负责的推送 |
|------|----------|--------|------|-----------|
| Fusion Engine | `.venv` v3.10 | `WeComNotifier` | Markdown | 融合决策、准实时速报、量化信号 |
| MindLynx | `systems/.../.venv` v3.12 | `WechatSender` | Markdown/Text/Image/File | 个股分析、大盘复盘PDF、评级PDF |

**为何不合并**: 两个 venv 有独立的 Python 版本和依赖集（上游项目约束）。跨 venv 调用通过 `subprocess` 实现（如 `run_daily.py` 调用 ML 的评级 PDF 生成器），这是进程隔离的合理模式，不是 hack。

### 4.3 推送消息类型

| 类型 | 引擎 | 格式 | 示例前缀 |
|------|------|------|---------|
| 融合决策 | Fusion | Markdown, L7 分组 | `🛟15:30融合决策` |
| 准实时速报 | Fusion | Markdown, 仅变化 | `🛟10:30融合速报` |
| 量化信号 | Fusion | Markdown, 每只一行(via WeComNotifier) | `🧬15:15量化信号` |
| 大盘复盘 | MindLynx | 文字摘要 + PDF | `🎯15:45大盘复盘` |
| 个股分析 | MindLynx | Markdown 仪表盘 | `👾14:30盘中报告` |
| 评级报告 | MindLynx | 文字通知 + PDF | `💰东方财富自选股评级报告` |
| 每日要闻 | MindLynx | Markdown, 按股分组 | `📰每日要闻` |
| 周末要闻 | MindLynx | Markdown, 按股分组 | `📰周末要闻` |
| 盘中告警 | MindLynx | Markdown 简短 | `🚨ATR止损`/`📈均线突破` |

---

## 五、关键设计决策记录

### D1: 为什么用双 venv？

**决策时间**: 项目初始化时（2026-05-30）
**原因**: 三个子系统是独立上游项目，各自有依赖要求：
- `MindLynx-Aistock` 需要 Python 3.12 + 特定版本 `akshare`/`weasyprint`
- Fusion 引擎使用 Python 3.10 + `scikit-learn`/`pandas`
- 合并 venv 会导致依赖冲突
**判定**: ✅ 正确的设计。跨 venv 调用通过 subprocess（进程隔离）是合理的集成模式。

### D2: 为什么有两层调度？

**决策时间**: 项目初始化时
**原因**: 
- **外层** (systemd timer): 管理跨系统、定时的一次性任务。systemd 提供日历表达式、重启策略、超时控制、日志集成，比应用级调度器更可靠。
- **内层** (ML Scheduler): 管理系统内部的复杂定时策略（交易日感知、多时点、动态调度时间变更）。
**判定**: ✅ 职责清晰，不是冗余。c1skill 审阅确认"合并进单调度器"的收益极低且风险不可接受。

### D3: 为什么推送配置统一到 .env？

**决策时间**: 2026-06-06（本次审计后修复）
**原因**: 三个子系统各自独立配置 webhook URL，更换时需改 3 处，漏改导致推送静默失效。
**修复**: 统一到项目根 `.env`，两个变量名（`WECOM_WEBHOOK_URL`/`WECHAT_WEBHOOK_URL`）指向同一值。
**判定**: ✅ 已修复。高风险问题，修复成本 15 分钟。

### D4: 为什么 realtime-fusion 需要周末跳过？

**决策时间**: 2026-06-06（本次审计后修复）
**原因**: `realtime_fusion.py` 的 `run_daemon()` 无交易日检测，周末 7x24 空转（每天 288 次无意义扫描）。虽仅消耗 ~15MB 内存，但属于不必要的资源占用。
**修复**: 添加 `_is_trading_day()` 和 `_seconds_until_next_trading_day()` 方法，非交易日睡眠到下一个周一 09:33。
**判定**: ✅ 已修复。

### D6: 为什么合并 daily/weekend highlight 推送函数？

**决策时间**: 2026-06-06
**原因**: `_push_daily_highlights()` 和 `_push_weekend_highlights()` 代码结构完全相同（按股分组 → 拼接 → notifier.send），仅标题/日志/去重 key 不同。重复 ~40 行。
**修复**: 重构为共享 `_push_highlights()` 通用函数，两处调用点各传不同参数。
**判定**: ✅ 已修复。

### D7: 为什么 lynx_signal 改用 WeComNotifier？

**决策时间**: 2026-06-06
**原因**: `lynx_signal.push_wecom()` 通过独立 `requests.post` 直接调用 webhook，不走统一发送路径。错误处理仅 `print()`，无日志/重试。
**修复**: 改为 `WeComNotifier.send_markdown()`，与 Fusion 其他推送共享相同的发送逻辑。
**判定**: ✅ 已修复。

### D5: 为什么不从 0 重建？

**决策时间**: 2026-06-06
**原因**: 
1. 所有真实痛点已修复或可低成本修复（周末空转 15 分钟可修）
2. 子系统是上游独立项目，无法"重建"掉
3. 重建 2-4 周 vs 剩余痛点价值 ~¥2/月电费，ROI 极低
**判定**: ✅ 不重建，增量优化。

---

## 六、项目目录结构

```
Aistock_vnpy_Trading/
│
├── src/                        # 融合引擎 (Fusion venv)
│   ├── fusion_engine.py        # 融合算法核心
│   ├── normalizer.py           # 信号归一化 (L7 映射)
│   ├── data_loader.py          # 零侵入三系统数据读取
│   ├── wecom_notifier.py       # 企业微信推送 (Fusion 端)
│   ├── realtime_fusion.py      # 准实时文件交换区扫描
│   ├── feature_bridge.py       # 可选功能 (龙虎榜/评级)
│   ├── reliability.py          # 置信度校准 + 幻觉检测
│   ├── logger.py               # CSV/JSON 持久化
│   ├── mind_agent_wrapper.py   # TradingAgent 封装
│   └── mind_stock_config.py    # A 股代码映射
│
├── scripts/
│   ├── run_daily.py            # 每日融合执行入口 (19:00)
│   ├── write_ly_signal.py      # 写 ly 信号到文件交换区
│   ├── backtest.py             # 回测
│   └── deploy-systemd.sh       # systemd 部署脚本
│
├── config/
│   ├── settings.yaml           # 融合配置
│   ├── stock_pool.csv          # 10 只股票池
│   ├── systems.yaml            # 路径映射
│   └── systemd/                # 所有 systemd 单元
│       ├── *.service           # 7 个服务
│       └── *.timer             # 4 个定时器
│
├── systems/                    # 三个深度定制子系统
│   ├── lynx_vnpy/              # ly: RandomForest 量化
│   │   └── lynx_signal.py      #    量化信号主程序
│   ├── MindLynx-Aistock/       # ml: AI 分析
│   │   ├── main.py             #    主入口 (调度器/监控)
│   │   ├── src/
│   │   │   ├── notification.py #    通知服务 (多通道)
│   │   │   ├── md2img.py       #    Markdown→PDF/图片
│   │   │   ├── scheduler.py    #    内部定时调度器
│   │   │   └── core/
│   │   │       ├── pipeline.py #    分析流水线
│   │   │       ├── market_review.py # 大盘复盘
│   │   │       └── pipeline_notification.py # 推送混合
│   │   └── scripts/
│   │       └── generate_rating_report.py # 评级PDF生成
│   └── mind_TradingAgent/      # at: 多智能体辩论
│       └── mind_tradingagent/
│           └── dataflows/
│               └── xueqiu.py   # 雪球/东方财富数据源
│
├── data/
│   ├── realtime/               # 文件交换区 (ly/ml/at 信号)
│   └── unified_cache/          # SQLite 共享缓存
│
├── docs/
│   ├── wechat_push_architecture.md  # 推送架构详细文档
│   └── push_architecture_review.md  # c1skill 审阅报告
│
├── .env                        # 环境变量 (webhook 单点维护)
├── .env.example                # 环境变量示例
└── AGENTS.md                   # GitNexus 集成指南
```

---

## 七、运维备忘

### 7.1 更换企业微信 webhook

```bash
# 只需改一个文件
vim .env
# 修改 WECOM_WEBHOOK_URL 和 WECHAT_WEBHOOK_URL 的值

# 重启使用推送的 systemd 服务
systemctl --user daemon-reload
systemctl --user restart Aistock_vnpy_Trading-scheduler.service
systemctl --user restart Aistock_vnpy_Trading-realtime-fusion.service
# lynx-signal 和 fusion 为 oneshot，下次触发自动使用新值
```

### 7.2 查看推送日志

```bash
# Fusion 端推送日志
tail -f config/logs/fusion-cron.log        # 19:00 融合决策
tail -f config/logs/realtime-fusion.log    # 准实时速报
tail -f config/logs/lynx-signal.log        # 量化信号

# MindLynx 端推送日志（journald）
journalctl --user -u Aistock_vnpy_Trading-scheduler.service --since today
journalctl --user -u Aistock_vnpy_Trading-monitor.service --since today
```

### 7.3 查看所有服务状态

```bash
systemctl --user list-units --all | grep aistock
```

---

## 八、术语说明

| 缩写 | 全称 | 说明 |
|------|------|------|
| ly | lynx_vnpy | RandomForest 量化信号系统 |
| ml | MindLynx-Aistock | 因子+LLM AI 分析系统 |
| at | mind_TradingAgent | 多智能体辩论交易系统 |
| L7 | 7 级决策空间 | [-3, +3] 范围，7 级信号映射 |
| Fusion | Aistock_vnpy_Trading | 三系统融合引擎 |
| scheduler | ML scheduler | MindLynx 内部定时调度器 |
| wecom | 企业微信 | WeChat Work 推送渠道 |
