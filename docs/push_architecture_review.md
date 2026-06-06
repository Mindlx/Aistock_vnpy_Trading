# 企业微信推送架构评估报告

> 分析日期: 2026-06-06

---

## 一、现状全景

```
┌─────────────────────────────────────────────────────────────────┐
│                        企业微信群                                │
│           key=2eb957ab-bc5b-4f99-8db9-b0fcb20ad44f              │
└────────────────────────────┬────────────────────────────────────┘
                             │
         ┌───────────────────┼───────────────────┐
         │                   │                   │
    ┌────▼────┐        ┌────▼────┐        ┌─────▼─────┐
    │ Fusion  │        │ lynx    │        │ MindLynx  │
    │ Engine  │        │ signal  │        │ Engine    │
    ├─────────┤        ├─────────┤        ├───────────┤
    │ settings│        │ .env    │        │ .env      │
    │ .yaml   │        │ root    │        │ ML/       │
    │ webhook │        │ WEBHOOK │        │ WEBHOOK   │
    │ =A      │        │ =A      │        │ =A        │
    └────┬────┘        └────┬────┘        └─────┬─────┘
         │                  │                    │
    ┌────▼────┐        ┌────▼────┐        ┌─────▼─────┐
    │WeCom    │        │requests │        │Wechat     │
    │Notifier │        │.post()  │        │Sender     │
    │.py      │        │直接发送 │        │.py        │
    └─────────┘        └─────────┘        └───────────┘
```

### 关键事实

| 系统 | 配置位置 | 环境变量名 | Python venv | 推送能力 |
|------|---------|-----------|------------|---------|
| Fusion Engine | `config/settings.yaml:wecom.webhook_url` | - | `.venv` v3.10 | Markdown |
| lynx_signal | `.env`(项目根) | `WECOM_WEBHOOK_URL` | `.venv` v3.10 | Markdown |
| MindLynx | `systems/MindLynx-Aistock/.env` | `WECHAT_WEBHOOK_URL` | `systems/.../.venv` v3.12 | Markdown, Text, Image, **File** |

**三个配置指向同一个 Webhook URL**。Webhook 本质上只是一个 API 端点，无状态，无队列，谁先发谁先到。

---

## 二、现有架构的痛点

### 2.1 配置三叉戟（高风险）

一个 webhook key 散落在三个配置文件中。如果要更换 webhook（比如密钥轮转、换群），必须：

```
❌ config/settings.yaml       → wecom.webhook_url
❌ .env                       → WECOM_WEBHOOK_URL (注意: 这里叫 WECOM 不是 WECHAT)
❌ systems/MindLynx-Aistock/.env → WECHAT_WEBHOOK_URL
```

**漏改一个 → 部分推送静默失效。** 且三个地方的环境变量名还不一致（`WECOM` vs `WECHAT`），容易混淆。

### 2.2 无推送队列（消息丢失风险）

三个系统各自直接 HTTP POST 到企业微信：

```
Fusion   ──HTTP POST──▶ 企业微信 API
lynx     ──HTTP POST──▶ 企业微信 API  
MindLynx ──HTTP POST──▶ 企业微信 API
```

- 如果网络抖动/API限流 → 各自独立重试逻辑不同，有些会丢消息
- 企业微信 API 有频率限制（每个 webhook 20次/分钟），三个发送者无法协调
- Fusion 通过 `subprocess` 调用 MindLynx venv 的脚本 — 跨 venv 调用不够优雅

### 2.3 消息顺序无法保证

```
19:00:02 Fusion 发融合决策 Markdown
19:00:03 subprocess 启动 generate_rating_report.py (MindLynx venv)
19:00:20 MindLynx 的 scheduler 也发了一条大盘复盘
19:00:35 generate_rating_report.py 发 PDF → 实际排在 19:00:20 的后面
```

用户看到：文字在前 → 别人的消息 → PDF 在后。顺序不可控。

### 2.4 推送逻辑碎片化

| 能力 | Fusion | lynx_signal | MindLynx |
|------|--------|-------------|----------|
| 重试机制 | 无 | 无 | 无 |
| 推送日志 | print | print | logger |
| 限流保护 | 无 | 无 | 无 |
| 健康检查 | 无 | 无 | 有(通知诊断) |
| 消息去重 | 无 | 无 | 有(dedup) |
| 格式统一 | - | - | - |

### 2.5 跨 venv 调用 Hack

```python
# run_daily.py 第 484-490 行
subprocess.run([
    str(_venv / "bin/python"),   # MindLynx 的 venv
    str(_script)                  # generate_rating_report.py
], timeout=300)
```

- 硬编码 venv 路径，不可维护
- subprocess 超时 300s，无法获得实时进度
- 跨 venv 无法共享内存中的配置和数据

---

## 三、方案对比

### 方案 A：现状（不修改）

**优点**: 零改动

**缺点**: 全部 2.1-2.5 痛点。

**评分**: ⭐⭐

---

### 方案 B：统一配置源（最小改动）

将 webhook URL 统一到一个来源，其他系统引用它。

**做法**:
```
1. 只在 .env (项目根) 维护 WECOM_WEBHOOK_URL
2. config/settings.yaml 改为 ${WECOM_WEBHOOK_URL} 或 os.getenv 读取
3. MindLynx/.env 改为 export WECOM_WEBHOOK_URL=$(grep ... /proc/.../env)
   或干脆软链: 删掉 MindLynx/.env 中的 WECHAT_WEBHOOK_URL，
   让 MindLynx 的 config.py 也读取同一来源
4. 统一环境变量命名（全叫 WECOM_WEBHOOK_URL）
```

**优点**:
- 改动极小
- 消除配置不一致风险

**缺点**:
- 仍无法解决队列/顺序/限流/重试问题
- 跨 venv subprocess 仍存在

**评分**: ⭐⭐⭐

---

### 方案 C：轻量通知代理服务（推荐）

新增一个独立的 systemd 通知代理服务，作为所有推送的唯一出口。

**架构**:
```
                    ┌──────────────────────────────┐
                    │     Notify Agent Service     │
                    │  (systems/notification-agent/)│
                    │                              │
                    │  ┌──────────┐  ┌──────────┐  │
Fusion  ──HTTP─────▶│  │ REST API │  │ Queue    │  │──▶ 企业微信 Webhook
lynx    ──HTTP─────▶│  │ :19999   │  │ (内存)   │  │
MindLynx──HTTP─────▶│  └──────────┘  └──────────┘  │
generate_ratings.py─▶└──────────────────────────────┘
```

**实现要点**:

```
systems/notification-agent/
├── server.py              # FastAPI 服务 (独立 venv 或复用任一 venv)
├── queue.py               # 内存队列 + 亚秒级消费
├── sender.py              # 企业微信发送 (含重试+限流)
├── config.yaml            # 统一 webhook 配置
└── notify-agent.service   # systemd 服务
```

**API 设计**（极简）:

```json
POST http://localhost:19999/push
{
  "msgtype": "markdown",     // markdown | text | file | image
  "content": "## 标题\n内容",  // markdown/text 内容
  "file": null,              // base64 文件数据 (file 类型)
  "filename": "report.pdf",  // 文件名
  "priority": "normal",      // high | normal | low
  "source": "fusion"         // 来源标识, 用于日志
}
```

返回:
```json
{"status": "queued", "id": "msg_xxx", "position": 1}
```

**优势**:

| 维度 | 现状 | 通知代理 |
|------|------|---------|
| 配置管理 | 3 处散落 | **1 处统一** |
| 推送队列 | 无 | **有序 FIFO** |
| 速率限制 | 无 | **内置令牌桶** |
| 重试策略 | 无 | **指数退避** |
| 推送日志 | print/logger 散落 | **统一审计日志** |
| 消息顺序 | 不可控 | **可控 FIFO** |
| 健康检查 | 无 | **/health 端点** |
| 跨 venv 调用 | subprocess hack | **HTTP 统一** |
| 新增推送方 | 需改配置 | **POST 即可** |

**对子系统的入侵度**:

| 系统 | 改动 | 影响 |
|------|------|------|
| Fusion `wecom_notifier.py` | `send_markdown()` 改为 POST 到代理 | 1 处改动 |
| Fusion `run_daily.py` | subprocess 调用改为 POST | 1 处改动 |
| lynx `lynx_signal.py` | `push_wecom()` 改为 POST | 1 处改动 |
| MindLynx `wechat_sender.py` | `send_to_wechat*()` 改为 POST | 3-4 处改动 |
| MindLynx `market_review.py` | 无改动（通过 wechat_sender 间接） | 0 处 |
| MindLynx `generate_rating_report.py` | 无改动（通过 wechat_sender 间接） | 0 处 |

**增加的系统复杂度**:
- 新增 1 个 systemd 服务（轻量 FastAPI）
- 新增 1 个目录 `systems/notification-agent/`（~100 行代码）
- 可在任一现有 venv 运行，无需新 venv

**缺点**:
- 引入新的服务依赖
- FastAPI 带来 asyncio 生态依赖
- 通知代理挂了 → 所有推送中断（可通过退化为直连兜底）

**评分**: ⭐⭐⭐⭐⭐

---

### 方案 D：共享 SDK 库（纯 Python 方案）

不新增服务，而是将推送逻辑提取为独立的 Python 包，各系统通过 `PYTHONPATH` 共享。

**做法**:
```
lib/notify/
├── __init__.py
├── sender.py        # 统一发送逻辑(限流+重试+日志)
├── config.py        # 从统一位置读 webhook (项目根 .env)
└── models.py        # 消息类型定义
```

- Fusion: `from lib.notify import send_markdown` 替代 `WeComNotifier`
- lynx_signal: `from lib.notify import send_markdown` 替代 `requests.post`
- MindLynx: `from lib.notify import send_markdown, send_file` 替代 `WechatSender`
- 通过 `PYTHONPATH` 或 `sys.path` 让两个 venv 都能导入

**优点**:
- 零新增服务
- 零新增依赖
- 共享限流/重试/日志逻辑
- 配置统一（读同一个 .env）

**缺点**:
- 仍无法保证跨系统消息顺序（各系统各自调用，没有队列）
- `PYTHONPATH` 跨 venv 共享需要额外配置
- MindLynx 的 venv 和 Fusion 的 venv 可能依赖冲突
- 无法优雅处理「先文字后文件」的顺序约束

**评分**: ⭐⭐⭐

---

### 方案 E：文件交换区改进（沿用现有模式）

当前已有 `data/realtime/` 文件交换区用于 ly/ml/at 信号交换。可以扩展这个模式做推送：

```
各系统推送请求 → 写入 data/push_queue/ → 单一消费 daemon → 企业微信
```

**做法**:
- 新增 `scripts/push_daemon.py`，监听 `data/push_queue/` 目录
- 各系统改为写入 JSON 文件到该目录（含顺序号）
- 消费 daemon 按顺序读取 → 发送 → 归档/删除

**优点**:
- 完全沿用现有模式，无新依赖
- 消息顺序通过文件名时间戳控制
- 单一发送者，天然限流

**缺点**:
- 文件 IO 比 HTTP 慢
- 轮询有延迟（秒级）
- 文件清理需要额外维护

**评分**: ⭐⭐⭐⭐

---

## 四、推荐方案：方案 C（通知代理服务）

### 理由

1. **最专业**：消息队列 + 统一出口是业界标准模式
2. **入侵最小**：每个子系统只需改 1 个文件（把发送目标的 URL 换一下）
3. **副作用最大**：解决所有 2.1-2.5 痛点
4. **未来可扩展**：后续如需增加 Telegram/飞书/钉钉通道，只需在代理端添加，无需修改子系统
5. **可观测性**：统一审计日志、/health 端点、Prometheus metrics（可后续加）

### 实施路径

```
Phase 1: 基础设施 (30 min)
├── systems/notification-agent/server.py    ← FastAPI, 2 个端点
├── systems/notification-agent/sender.py    ← 限流+重试+发送
├── systems/notification-agent/notify-agent.service  ← systemd
└── 单 webhook 配置 (统一到项目根 .env)

Phase 2: 系统对接 (30 min)
├── Fusion: wecom_notifier.py → POST to agent
├── lynx: lynx_signal.py → POST to agent
├── MindLynx: wechat_sender.py → POST to agent
└── Fusion: run_daily.py subprocess → 改为 POST + 顺序控制

Phase 3: 清理 (15 min)
├── 删除 generate_rating_report.py 中的 WechatSender 直接调用
├── 移除 settings.yaml 中的 webhook_url 字段
├── 移除 MindLynx/.env 中的 WECHAT_WEBHOOK_URL
└── 验证所有推送正常
```

### 不推荐的方案

- **方案 B**只解决配置问题，不解决核心架构问题
- **方案 D**受 venv 隔离限制，跨 venv 共享库可能引入依赖冲突
- **方案 E**虽可行但不够优雅，文件 IO 延迟和轮询开销

---

## 五、决策树

```
需要改动推送架构吗？
├─ 否 → 保持现状（但至少做方案 B 统一配置）
└─ 是 →
   ├─ 追求最小改动 → 方案 B + 方案 D（统一配置 + 共享 SDK）
   └─ 追求专业方案 →
      ├─ 有 FastAPI 经验 → 方案 C（通知代理，推荐）
      └─ 不想新增服务 → 方案 E（文件队列）
```
