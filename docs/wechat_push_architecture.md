# 企业微信推送架构全面梳理

> 最后更新: 2026-06-06

---

## 一、整体架构概览

两套独立引擎，各自拥有独立 venv 和企业微信 Webhook，推送到**同一个企业微信群**。

```
┌───────────────────────────────────────────────────┐
│                   企业微信群                         │
└──────────┬──────────────────────────────┬──────────┘
           │                              │
    ┌──────▼──────┐              ┌───────▼───────┐
    │ Fusion 引擎  │              │ MindLynx 引擎 │
    │ .venv v3.10  │              │ .venv v3.12   │
    │ src/         │              │ systems/       │
    │ webhook=A    │              │ webhook=B      │
    └──────┬───────┘              └───────┬────────┘
           │                              │
    ┌──────▼───────┐              ┌───────▼────────┐
    │ systemd timer │              │ systemd service │
    │ 19:00 触发    │              │ 常驻 + 内部调度  │
    └──────────────┘              └─────────────────┘
```

---

## 二、定时触发链路总表

### 2.1 Fusion 引擎 systemd 定时器

| # | Timer 文件 | 触发时间 | Service → Entry Script | 推送内容 |
|---|-----------|---------|----------------------|---------|
| 1 | `Aistock_vnpy_Trading-fusion.timer` | Mon-Fri 19:00 | `run_daily.py` | 融合决策 + 龙虎榜 + 东方财富评级 PDF |
| 2 | `Aistock_vnpy_Trading-lynx-signal.timer` | Mon-Fri 15:15 | `lynx_signal.py` | ML 量化信号 |
| 3 | `Aistock_vnpy_Trading-realtime-fusion.timer` | Mon-Fri 09:33 | `realtime_fusion.py --daemon` | 准实时融合(5分钟扫描) |

### 2.2 MindLynx 引擎 systemd 常驻服务（内部调度）

| # | Service 文件 | 类型 | Entry Script | 调度任务 | 推送内容 |
|---|-------------|------|-------------|---------|---------|
| 4 | `Aistock_vnpy_Trading-scheduler.service` | `Type=simple` 常驻 | `main.py --schedule` | `08:30` 日间情报(盘前) | 每日要闻 |
| | | | | `10:00/11:00/14:00/15:00` 整点全量分析 | 个股分析 |
| | | | | `11:45/15:45` 大盘复盘 | 大盘复盘 PDF |
| | | | | `Sun 20:00` 周末情报 | 周末要闻 |
| | | | | `Mon 07:30` 周末情报补量 | 周末要闻 |
| 5 | `Aistock_vnpy_Trading-monitor.service` | `Type=simple` 常驻 | `main.py --realtime-monitor-daemon` | 交易时段实时监控 | 盘中异动告警 |

### 2.3 其他 systemd 定时器

| # | Timer/Service | 触发时间 | 功能 | 是否推送 |
|---|--------------|---------|------|---------|
| 6 | `Aistock_vnpy_Trading-TA.timer` | Mon-Fri 09:31/13:00 | TradingAgent 深度论证 | 结果写入文件，不直接推送 |
| 7 | `Aistock_vnpy_Trading-ml-factor.service` | 常驻(5s) | ML 因子实时计算 | 不推送 |

---

## 三、推送接口清单

### 3.1 Fusion 引擎（src/，Python 3.10 venv）

#### 接口 1: `WeComNotifier.send_markdown(content: str)`
- **文件**: `src/wecom_notifier.py`
- **msgtype**: `markdown`
- **限制**: 企业微信 Markdown 上限 ~4096 字节
- **Webhook**: `settings.yaml` 中的 `wecom.webhook_url`

#### 接口 2: `WeComNotifier.push_daily_decision(results, date, extra_sections=None)`
- **文件**: `src/wecom_notifier.py`
- **底层**: 调用 `format_daily_summary()` + `send_markdown()`
- **数据**: 三系统融合结果（lynx+mindlynx+tradingagent）

#### 接口 3: ~~`run_xueqiu_sentiment(stock_codes) → str | None`~~（已废弃）
- **文件**: `src/feature_bridge.py`
- **状态**: 已废弃。原返回 Markdown 格式的东方财富评级文本，现已替换为 PDF 文件推送（通过 `generate_rating_report.py`）
- **保留原因**: 函数仍在 `feature_bridge.py` 中，但 `run_daily.py` 不再调用

#### 接口 4: `run_dragon_tiger(stock_codes, top_n) → str | None`
- **文件**: `src/feature_bridge.py`
- **返回**: Markdown 格式龙虎榜文本
- **调用方**: `run_daily.py`（作为 extra_sections 附加）

### 3.2 MindLynx 引擎（systems/MindLynx-Aistock/，Python 3.12 venv）

#### 接口 5: `WechatSender.send_to_wechat(content: str, *, timeout_seconds) → bool`
- **文件**: `src/notification_sender/wechat_sender.py`
- **msgtype**: `markdown` 或 `text`（由配置 `wechat_msg_type` 控制）
- **限制**: markdown 4096 字节 / text 2048 字节，超长自动分批

#### 接口 6: `WechatSender.send_to_wechat_file(file_bytes: bytes, filename: str) → bool`
- **文件**: `src/notification_sender/wechat_sender.py`
- **msgtype**: `file`
- **流程**: 上传 media → 获取 media_id → 发送 file 消息
- **用途**: PDF 报告推送（大盘复盘 PDF、东方财富评级 PDF）

#### 接口 7: `WechatSender.send_image(image_bytes: bytes) → bool`（内部 `_send_wechat_image`）
- **文件**: `src/notification_sender/wechat_sender.py`
- **msgtype**: `image`
- **限制**: 图片 base64 < 2MB

#### 接口 8: `NotificationMixin._send_notifications(results, report_type)`
- **文件**: `src/core/pipeline_notification.py`
- **底层**: 调用 `notifier.generate_wechat_dashboard()` → `wechat_sender.send_to_wechat()`
- **说明**: 多用户路由 + Markdown 转图片 + 多通道分发

#### 接口 9: `NotificationMixin._send_single_stock_notification(result, report_type)`
- **文件**: `src/core/pipeline_notification.py`
- **说明**: 单股推送（`--single-notify` 模式）

#### 接口 10: `run_market_review(notifier, ...)` 大盘复盘推送
- **文件**: `src/core/market_review.py`
- **流程**: 生成 Markdown → `_extract_summary_text()` 提取一行摘要 + `markdown_to_pdf()` → 先发 text 摘要 + 后发 PDF 文件
- **特点**: 先文字后文件，PDF 失败不影响文字

#### 接口 11: `_push_weekend_highlights()` 周末情报推送
- **文件**: `main.py`
- **底层**: 调用 `_push_highlights()` 通用推送函数
- **格式**: 按股票分组，每条带重要表情 + 名字 + 摘要
- **特殊逻辑**: `is_refresh=True` 时查询 DB 去重（周一补量模式）

#### 接口 12: `_push_daily_highlights()` 每日情报推送
- **文件**: `main.py`
- **底层**: 调用 `_push_highlights()` 通用推送函数
- **格式**: 按股票分组，每条带重要表情 + 名字 + 摘要

> 注: 接口 11/12 已重构为共享 `_push_highlights(notifier, highlights, *, title_text, footer_text, dedup_key_prefix, log_label)` 通用函数（2026-06-06 优化），消除 ~40 行重复代码。

### 3.3 lynx_vnpy 引擎（systems/lynx_vnpy/，Fusion 共用 venv）

#### 接口 13: `push_wecom(signals)` 量化信号推送
- **文件**: `systems/lynx_vnpy/lynx_signal.py`
- **底层**: 调用 `WeComNotifier.send_markdown()`（2026-06-06 统一，之前为独立 `requests.post`）
- **msgtype**: `markdown`
- **Webhook**: 环境变量 `WECOM_WEBHOOK_URL`（与 Fusion 共用）

---

### 3.4 2026-06-06 接口优化记录

| # | 优化内容 | 类型 |
|---|---------|------|
| 3 | `run_xueqiu_sentiment()` → **已废弃**（改为 PDF 文件推送） | 清理 |
| 11/12 | `_push_weekend_highlights` / `_push_daily_highlights` → **合并**为 `_push_highlights()` 通用函数 | 重构 |
| 13 | `lynx_signal.push_wecom()` → **改为** `WeComNotifier.send_markdown()` 统一发送路径 | 统一 |

---

## 四、消息格式示例

### 4.1 融合决策（Fusion Engine 19:00 推送）

```
## 🛟 16:30 融合决策｜有效10｜TA10

**⚡ 系统分歧**
🔴 **古麒绒材(001390)** ¥38.52 +2.34%｜ly+1.20 ml+2.10 at-0.50｜强烈看多｜仓位7成｜量比1.23｜支撑35.21 压力40.15

**🚀 强烈看多**
🔴 **中航机载(600372)** ¥45.80 +3.21%｜ly+2.50 ml+1.80 at+2.10｜强烈看多｜仓位8成

**📈 看多**
🟠 **雷迪克(300652)** ¥28.50 -0.50%｜ly+1.20 ml+0.80 at+0.50｜谨慎看多｜仓位5成｜量比0.80

**🗂 中性/持有**
⚪ **皖新传媒(601801)** ¥12.30 +0.10%｜ly+0.20 ml-0.10 at+0.30｜中性/持有｜仓位3成

**📉 看空**
🟢 **美迪西(688202)** ¥35.20 -1.50%｜ly-1.20 ml-0.80 at-1.50｜看空｜仓位1成

⏳ 降级2｜TA1
⚠ ly9/10 ml8/10 at7/10
```

### 4.2 东方财富评级（Fusion 19:00 extra_section → 现已改为独立 PDF）

旧版 Markdown 文本格式（已废弃，现替换为 PDF 文件推送）:
```
💰东方财富自选股评级报告已生成 📎 完整报告见附件PDF
```

### 4.3 东方财富评级 PDF 报告（独立文件推送）

推送顺序:
1. 先发文字: `💰东方财富自选股评级报告已生成 📎 完整报告见附件PDF`
2. 后发文件: `{date}东方财富评级报告.pdf`（PDF 文件，文件名含日期）

### 4.4 ML 量化信号（lynx_signal 15:15 推送）

```
🧬 15:15 ly量化信号
🔴 **中航机载** ¥45.80 +3.21%｜L7+2.50｜置信82.1%｜RSI65.2｜MACD↗｜ATR3.45%｜强烈看多
🟠 **古麒绒材** ¥38.52 +2.34%｜L7+1.20｜置信62.5%｜RSI55.1｜MACD↘｜ATR2.10%｜谨慎看多
⚪ **皖新传媒** ¥12.30 +0.10%｜L7+0.20｜置信50.2%｜RSI45.0｜MACD↗｜ATR1.20%｜中性/持有
🟢 **美迪西** ¥35.20 -1.50%｜L7-1.20｜置信28.0%｜RSI32.5｜MACD↘｜ATR4.10%｜看空

> 数据源: efinance
> 模型: RandomForest
```

### 4.5 大盘复盘（MindLynx 11:45/15:45 推送）

**文字摘要（先发）**:
```
🎯 15:45 大盘复盘（全天）：今日市场三大指数集体调整，沪指跌0.42%报收3028点，创业板指跌0.95%，两市成交额缩量至7516亿。 上期建议提示短线回调风险，今日走势符合预期。

📎 详细内容见PDF文档
```

**PDF 文件（后发）**:
- 文件名: `{date}大盘复盘报告_{session_label}.pdf`
- 文件类型: PDF（含板块 treemap 图表）

### 4.6 周末情报（周日 20:00 推送）

```
📰 周末要闻 | 主力采集
🔴 华大基因(300676) | 华大基因宣布新一轮回购计划-利好 | 基因测序获政策支持-利好
🔴 中航机载(600372) | 军工板块重组预期升温-利好 | 大股东增持完成-利好
🟡 美迪西(688202) | 医药板块整体承压-利空
📊 12条 | 完整分析下个交易日推送
```

### 4.7 每日情报（盘前 08:30 推送）

```
📰 每日要闻 | 盘前
🔴 平潭发展(000592) | 海南自贸区政策利好-利好 | 公司公告获得新项目-利好
🟡 *ST网达(603189) | 通信板块调整-中性
📊 5条 | 下个交易时段分析将自动注入
```

### 4.8 龙虎榜（Fusion 19:00 extra_section）

格式由 `build_dragon_tiger_prompt()` 生成，以 **🐉 龙虎榜** 标题开头。

### 4.9 个股分析报告（MindLynx 整点分析 10/11/14/15 推送）

由 `NotificationMixin._send_notifications()` 调用 `notifier.generate_wechat_dashboard()` 生成，格式为紧凑版个股分析摘要。

### 4.10 盘中实时监控告警（MindLynx monitor 服务）

由 `realtime_monitor` 服务触发，分三阶段推送：

**Phase 1 — 15 分钟简报**:
```
👾 10:30 盘中速报
🔴 **中航机载** ¥45.80 +2.35%｜评分75｜¥178.00~¥182.00｜量比1.5｜>MA5 >MA10｜走势↗
```

**Phase 2 — ATR 止损告警**:
```
🚨 10:30 ATR止损｜评分75
**贵州茅台** ¥178.00 -0.50% 跌破2.0×ATR ¥178.50
```

**Phase 3a — 量价异动**:
```
🔥 10:30异动预警｜评分75
**贵州茅台** ¥180.50 +2.35% 量比 3.2 换手率 5.1%
```

**Phase 3b — 均线突破/跌破**:
```
📈 10:30均线突破｜评分75
**贵州茅台** ¥180.50 +2.35% 突破MA5(¥178.00)
```

---

## 五、推送顺序策略

| 推送场景 | 顺序 | 说明 |
|---------|------|------|
| **融合决策(19:00)** | 先发融合决策 Markdown → 再发龙虎榜 extra(独立消息) → subprocess 发评级 PDF(先文字后文件) | 融合结果优先 |
| **大盘复盘(11:45/15:45)** | 先发文字摘要 → 再发 PDF 文件 | 摘要让用户快速了解 |
| **东方财富评级(19:00 via subprocess)** | 先发 `💰东方财富自选股评级报告已生成` 文字 → 再发 PDF 附件 | 文字通知在前，详情文件在后 |

---

## 六、venv 边界

| 引擎 | venv | Python | 推送类 | 支持的 msgtype |
|------|------|--------|-------|---------------|
| Fusion Engine | `.venv/` | 3.10 | `WeComNotifier` | **markdown** |
| MindLynx | `systems/MindLynx-Aistock/.venv/` | 3.12 | `WechatSender` | **markdown, text, image, file** |
| lynx_vnpy | `.venv/`(共用Fusion) | 3.10 | 直接 requests | **markdown** |

- **Fusion 引擎** 通过 `subprocess` 调用 **MindLynx venv** 下的脚本来实现 PDF 推送
- **lynx_vnpy** 使用自己的环境变量 `WECOM_WEBHOOK_URL` 独立推送

---

## 七、关键代码文件索引

```
# Fusion 引擎
src/wecom_notifier.py              → WeComNotifier（Markdown 推送核心）
src/feature_bridge.py              → run_xueqiu_sentiment, run_dragon_tiger（可选功能）
src/realtime_fusion.py             → RealtimeFusion（准实时融合速报）
scripts/run_daily.py               → 19:00 入口（融合+龙虎榜+评级PDF）

# MindLynx 引擎
systems/MindLynx-Aistock/
  src/notification_sender/wechat_sender.py  → WechatSender（文件/图片/文本/马克飞象）
  src/notification.py                       → NotificationService（多通道通知调度）
  src/config_notification.py                → NotificationConfig（微信相关配置）
  src/notification_routing.py               → 路由配置（report/alert/system_error）
  src/core/pipeline_notification.py         → NotificationMixin（个股/批量推送）
  src/core/market_review.py                 → run_market_review（大盘复盘PDF）
  src/md2img.py                             → markdown_to_pdf / markdown_to_image
  main.py                                   → 主入口（整点分析+周末/每日情报+大盘复盘）
  scripts/generate_rating_report.py         → 东方财富评级PDF报告生成器
  src/core/pipeline.py                      → StockAnalysisPipeline（整点分析调度）
  src/scheduler.py                          → Scheduler（内部定时调度）
  src/services/realtime_monitor.py          → RealtimeMonitorService（盘中3阶段告警）
  src/services/alert_worker.py              → ThresholdEventMonitor（阈值规则告警）
  bot/handler.py                            → handle_wecom_webhook（入站webhook处理）

# lynx_vnpy
systems/lynx_vnpy/lynx_signal.py     → push_wecom（量化信号独立推送）
systems/lynx_vnpy/lynx_vnpy/trader/wechat.py  → 已废弃iLink协议（非企业微信Webhook）

# 配置
config/settings.yaml                 → Fusion webhook + features 开关
systems/MindLynx-Aistock/.env        → MindLynx webhook + 功能配置
config/systemd/*.{service,timer}     → 所有 systemd 定时器
```
