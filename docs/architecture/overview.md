# Aistock_vnpy_Trading 系统架构文档

> 最后更新: 2026-07-02
> 覆盖: 三系统融合 + ly双模型IC加权 + v4.0精度校准 + op_advice退出L7裁决 + 分歧ML少数方增强

---

## 一、系统全景

```
┌──────────────────────────────────────────────────────────────────┐
│                     Aistock_vnpy_Trading                          │
│              三系统融合决策平台 (MIT License)                       │
│                                                                  │
│  ┌──────────────────── Data Warehouse ──────────────────────┐    │
│  │  services/data_warehouse/ (Phase 0-5)                    │    │
│  │  ├─ warehouse.py → WarehouseReader 统一读接口             │    │
│  │  ├─ limiter.py   → 跨进程令牌桶 (EM 15/min)              │    │
│  │  ├─ storage.py   → SQLite数据湖 8张表 (WAL模式)          │    │
│  │  ├─ scheduler.py → 定时刷新守护进程 (systemd)             │    │
│  │  └─ fetchers.py  → 多级降级链(EM→Sina→efinance)          │    │
│  └────────────────────────┬─────────────────────────────────┘    │
│                           │                                      │
│  ┌──────────────┐  ┌──────┴──────┐  ┌──────────────────┐       │
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
│         │    │  alpha158_signal.json  │       │                 │
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

> **数据仓库数据流**: 系统中所有子系统在获取数据时, 优先通过 `WarehouseReader` 检查 `data_warehouse.db` 缓存; 缓存命中且未过期则直接返回 (零 API 调用), 否则降级调用原始 API 并回写缓存。所有 API 调用受令牌桶限流器保护。详见 [`docs/data-warehouse-implementation.md`](data-warehouse-implementation.md)。

---

## 二、子系统职责与边界

| 子系统 | 方法 | 频率 | 核心输出 | 独立性 |
|--------|------|------|---------|--------|
| **ly** (lynx_vnpy) | RandomForest+LGB 双模型集成 + 15TA+58Alpha158因子 | 日频/准实时 | 上涨概率 + L7 信号 | 独立推送 |
| **ml** (MindLynx-Aistock) | 12因子+15策略+LLM 推理 | 日频/实时 | 综合评分 0-100 (op_advice纯文本,不参与融合) | 独立 venv, 独立推送 |
| **at** (mind_TradingAgent) | 多智能体辩论 (LangGraph) | 盘后 (09:31/13:00) | 5 级评级 | 独立 venv |

### 核心原则

> **三子系统必须保持完整独立** — 不修改子系统代码、不引入跨系统依赖、各自的 venv 和配置各自管理。

Fusion Engine 通过两种方式集成：
1. **零侵入数据加载** (`src/data_loader.py`) — 读子系统输出文件，不调子系统 API
2. **文件交换区** (`data/realtime/`) — 准实时融合通过 JSON 文件交换信号

### LLM 模型配置

AT 系统默认使用本地模型 (Qwen3.6-27B, 2×RTX 3090, llama.cpp)：
- 配置文件: `systems/mind_TradingAgent/.env`
- 推理端点: `http://localhost:15433/v1` (llama.cpp server, Docker)
- 结构化输出: 不原生支持, 系统自动回退到 free-text 生成

**如需切回 DeepSeek API**:
```bash
cp systems/mind_TradingAgent/.env.deepseek-backup systems/mind_TradingAgent/.env
```
> `.env` 文件不提交到 Git (含 API Key), 备份为 `.env.deepseek-backup`。

---

## 三、部署架构

**重要区分：实时 vs 准实时**

系统中有两种"实时"能力，各自独立运行，解决不同问题：

| 层级 | 触发方式 | 延迟 | 推送内容 | 组件 |
|------|---------|------|---------|------|
| **ML实时预警** | WebSocket事件驱动 | 秒级 | 个股ATR止损/均线突破/量价异动 | monitor.service → realtime_monitor.py |
| **Fusion准实时融合** | 文件轮询(300s) | 分钟级 | 三系统融合综合得分变化 | realtime-fusion.service → realtime_fusion.py |

ML实时预警是真正的**事件驱动实时**：行情一跳就检查止损、均线、量价条件，触发即推送。Fusion准实时融合是**轮询驱动近实时**：每5分钟扫文件交换区，融合三系统得分，超阈值才推送。前者用于风控止损，后者用于三系统信号同步后的综合评分更新。

**准实时融合的价值评估**

文件交换区三信号的更新频率：
- ly_signal.json: 每日一次 (15:15) — RF+LGB双模型集成，盘中固定
- ml_signal.json: 每5分钟 — 12因子层读stock_daily DB，有新数据就变
- at_signal.json: 每日两次 (09:31/13:00) — LLM辩论跑完即固定
- alpha158_signal.json: 每5分钟 — 58Alpha158因子+LGB推理，纯数学无LLM

所以盘中真正频繁变化的只有ml因子信号。那准实时融合的价值不在于"更快发现行情变化"（这事ML实时预警已经做到了），而在于**把ml因子信号放到三系统坐标系中做上下文解读**：

1. **共识漂移监测**：ml因子从+1变成+2，在ly已是+2的背景下只是确认；在ly为-1.5时则是分歧加剧——准实时融合会触发ML少数方增强
2. **分歧跟踪**：ML实时预警只看单只个股的技术面触发，看不到系统方向矛盾。准实时融合知道"ly看空、ml转多、at中立"，会标记分歧
3. **仓位建议联动**：ML预警说"止损触发了"，准实时融合说"综合得分从+1.2降到-0.3，建议仓位从0.5-1成降到0成"——不同层面的信息

**局限**：这不是真正实时，值也小于其名字给人的预期。信号源变化不频繁，经常5分钟扫描一次发现无变化就跳过。它的定位应该是"盘中融合坐标系下的共识漂移监测"，不是"更快发现行情"。

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
│  │  ├── config/stock_pool.csv         → stock_pool.csv (单源配置)│  │
│  │  └── data/realtime/                → 文件交换区 (ly/ml/at)  │  │
│  └────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 systemd 服务清单

| 服务 | 类型 | Venv | 内存 | 周末运行 | 说明 |
|------|------|------|------|---------|------|
| `scheduler.service` | 常驻 daemon | MindLynx | ~75MB | ❌ 智能跳过 | 内部调度 10 个定时任务 |
| `monitor.service` | 常驻 daemon | MindLynx | ~13MB | ❌ 智能跳过 | WebSocket 盘中监控 |
| `ml-factor.service` | 常驻 daemon | Fusion | ~15MB | ⚠️ 运行但无操作 | 12因子计算 daemon |
| `alpha158-service.service` | 常驻 daemon | Fusion | ~15MB | ⚠️ 运行但无操作 | 58Alpha158因子+LGB daemon |
| `realtime-fusion.service` | 常驻 daemon | Fusion | ~15MB | ❌ 周末跳过 | 文件交换区扫描 (v2 新增) |
| `fusion.service` | oneshot | Fusion | - | ❌ 仅工作日 | 19:00 日终融合 |
| `lynx-signal.service` | oneshot | Fusion | - | ❌ 仅工作日 | 15:15 量化信号 |
| `TA.service` | oneshot | Fusion | - | ❌ 仅工作日 | 09:31/13:00 TA 分析 |

**总计常驻内存**: ~118MB（scheduler 75MB + monitor 13MB + ml-factor 15MB + realtime-fusion 15MB）

### 3.3 定时触发时间线（标准交易日）

```
09:00 ─ scheduler ─── 日间情报(盘前)推送
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

### D8: 12因子 vs 58Alpha158 — 为什么ml和ly用不同的因子集？

**决策时间**: 2026-06-07
**原因**: 
- **ml的12因子**由因子专家手工精选，每个概念一个（illiquidity/Amihud非流动性、max_effect/极端收益、volume_trend/量价相关性、volatility_ratio/波动率结构等）。ml子系统通过LLM消费这些因子进行综合决策。
- **ly的58Alpha158因子**来自vnpy库(Qlib方法论)，体系化覆盖：K线形态(kmid/klen/kup/klow/ksft)、多窗口滚动统计(MA/STD/MAX/MIN/RSV，5个窗口×每个)、涨跌统计(cntp/cntn/cntd)等。ly子系统通过LGB模型消费这些因子进行概率预测。

**两者不是超集/子集关系**，而是互补关系：
- 12因子中有8个概念(volume_trend/illiquidity/max_effect/volatility_ratio等)是58因子没有的
- 58因子中K线形态/多窗口/分位数等体系化覆盖是12因子没有的

**判定**: ✅ 正确的设计。两个子系统各自使用更适合自身消费方式的因子集，通过融合引擎对不同信号投票——这正是三系统融合架构的核心优势：互补独立，各取所长。

### D9: 15个TA指标的来源与理论基础

**决策时间**: 2026-06-07
**说明**: ly子系统中RF模型使用的15个TA指标全部手写在 `lynx_signal.py` 的 `compute_features()` 中，不是来自vnpy库或任何上游项目。但每个指标都有深厚的理论来源：

| 指标 | 来源 | 首次提出 | 验证 |
|------|------|---------|------|
| ret_1d/5d/10d/20d | 动量因子 (Fama-French, Jegadeesh-Titman) | 1993 | 学术引用10000+ |
| ma5_dist/ma20_dist/ma_cross | 道氏理论 (Charles Dow) | 1880s | 技术分析始祖 |
| rsi14 / atr_ratio | Welles Wilder | 1978 | 公认经典 |
| macd/macd_signal/macd_hist | Gerald Appel | 1970s | 趋势跟踪核心 |
| boll_pos | John Bollinger | 1980s | 波动率通道标准 |
| cci20 | Donald Lambert | 1980 | 超买超卖指标 |
| vol_ratio | 量价分析传统 | 1900s | 基础分析工具 |

**与58Alpha158的关系**: 不是"手写 vs 权威"，而是"经典技术分析 vs 体系化数学表达"。两者在ly内部由RF和LGB分别消费，互补参与融合决策。

### D10: 三系统 — 三种完全不同的方法论

**决策时间**: 2026-06-07
**说明**: ly、ml、at三个子系统使用完全不同的方法论分析同一只股票，这正是三系统融合架构的核心设计理念：

```
15 TA       → RF        → ly得分 ─┐
58 Alpha158 → LGB       → ly得分 ─┤
12 ml因子   → LLM+策略   → ml得分 ─┤──→ fusion engine → L7决策
新闻/情绪    → 多智能体    → at得分 ─┘
基本面       → 辩论系统
               (分析师/研究员/风控/PM)
```

at不使用因子。它的方法论与ly和ml完全不同：
- ly = 纯量价数学模型（RF消费15TA, LGB消费58Alpha158）
- ml = 因子+LLM混合模型（12因子注入LLM Prompt，15个YAML策略Agent）
- at = 多智能体定性研究（分析师→研究员→交易员→风控→PM，全部LLM驱动）

三种方法论独立互补：同一个股票，ly从量价技术面看、ml从因子+AI分析看、at从多智能体辩论看。当三者形成共识时信号可靠性显著提升——这就是三系统融合的意义。

此外，**东方财富数据**（散户情绪+机构行为+截面价值）已作为独立数据源注入 ML 的 LLM prompt，不参与融合投票。详见 `docs/eastmoney/c1skill-analysis.md`。

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
│   ├── normalizer.py           # 信号归一化 (L7 映射 + v4.0精度校准)
│   ├── data_loader.py          # 零侵入三系统数据读取
│   ├── wecom_notifier.py       # 企业微信推送 (Fusion 端)
│   ├── realtime_fusion.py      # 准实时文件交换区扫描
│   ├── feature_bridge.py       # 可选功能 (龙虎榜/评级)
│   ├── reliability.py          # 置信度校准 + 幻觉检测
│   ├── market_data_fallback.py # 回测多源数据fallback链 (🆕)
│   └── logger.py / unified_cache.py / mind_agent_wrapper.py / mind_stock_config.py
│
├── scripts/
│   ├── run_daily.py            # 每日融合执行入口 (19:00)
│   ├── write_ly_signal.py      # 写 ly 信号到文件交换区
│   ├── backtest.py             # 回测
│   └── deploy-systemd.sh       # systemd 部署脚本
│
├── services/
│   ├── ml_factor_service.py      # 12因子纯数学信号 (每5分钟)
│   ├── alpha158_service.py       # 58Alpha158因子+LGB信号 (每5分钟)
│   ├── data_warehouse/           # 数据湖: 统一OHLCV缓存+限流
│   └── eastmoney/                # 东方财富数据服务 (自包含,无子系统依赖)
│       ├── fetcher.py            # 数据获取+缓存+全市场快照存档
│       └── research.py           # 全维度IC分析(≥20天数据后)
│
├── data/
│   ├── realtime/                 # 实时信号交换 (ly/ml/at/eastmoney)
│   ├── backtest/                 # 融合回测数据库
│   ├── fusion_output/            # 每日融合结果
│   ├── research/                 # 研究数据统一目录
│   │   └── eastmoney_snapshot/   # 东方财富全市场日频快照
│   ├── unified_cache/            # OHLCV 缓存 (数据湖)
│   └── ...
├── config/
│   ├── settings.yaml           # 融合配置
│   ├── stock_pool.csv          # stock_pool.csv (单源配置)
│   ├── systems.yaml            # 路径映射
│   └── systemd/                # 所有 systemd 单元
│       ├── *.service           # 9 个服务
│       └── *.timer             # 8 个定时器
│
├── systems/                    # 三个深度定制子系统
│   ├── lynx_vnpy/              # ly: RandomForest 量化
│   │   └── lynx_signal.py      #    量化信号主程序 (RF+LGB双模型)
│   │   ├── models/              #    RF+LGB模型pkl
│   │   ├── vnpy_bridge/         #    Alpha158因子管线
│   │   │   ├── data_converter.py    # DB→Parquet数据桥接
│   │   │   ├── run_alpha_pipeline.py# Alpha158+LGB+IC管线
│   │   │   ├── alpha_predictor.py   # 58因子+LGB推理模块
│   │   │   └── ly_backtest.py       # 独立策略回测
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
│   ├── realtime/               # 文件交换区 (ly/ml/at/alpha158 信号)
│   │   ├── ly_signal.json
│   │   ├── ml_signal.json
│   │   ├── at_signal.json
│   │   └── alpha158_signal.json
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
| alpha158 | Alpha158因子服务 | 58因子+LGB纯数学因子信号通道 |
| L7 | 7 级决策空间 | [-3, +3] 范围，7 级信号映射 |
| Fusion | Aistock_vnpy_Trading | 三系统融合引擎 |
| scheduler | ML scheduler | MindLynx 内部定时调度器 |
| wecom | 企业微信 | WeChat Work 推送渠道 |
