# 系统资源画像与分发思考

> 首次分析日期: 2026-06-24
> 基线版本: Aistock_vnpy_Trading @ 14 commits

---

## 一、概述

本文档记录我们首次从**资源占用**和**分发角度**审视系统的结果。不仅记录技术指标，也反映我对系统定位的一贯理念：

**这个系统不追求极致的规模或效率，而是追求"恰好满足需求的最小代价"。**

这里的"最小代价"有两层含义：
1. **功能层面**：不做多余的事。每个模块都有明确的职责和退出路径（数据不存在时静默降级）
2. **资源层面**：不浪费硬件。但也不为节省几个 MB 去牺牲代码可读性或架构清晰度

东方财富数据的接入就是一个例子：单次 API 返回 5000+ 股票数据，我们用一两次调用就完成全部字段获取，不搞复杂的分布式爬虫。够用就好。

---

## 二、资源全景

### 2.1 磁盘占用

| 目录 | 大小 | 说明 |
|------|:----:|------|
| `.venv/` | **1.3 GB** | Fusion 融合引擎 venv（已清理 GPU 包） |
| `systems/MindLynx-Aistock/.venv/` | **1.0 GB** | ML 子系统 venv（含 litellm/httpx 等 LLM 依赖） |
| `systems/` 源码 | ~200 MB | 三个子系统代码 |
| 数据目录 `data/` | ~100 MB | 回测数据库、缓存、存档 |
| **总计** | **~2.6 GB** | |

> 首次分析时 Fusion venv 含 4.5GB GPU 包（nvidia/torch/triton），已清理。
> GPU 包是 litellm 的可选依赖，非本系统必需。

### 2.2 内存占用（RSS）

| 进程 | venv | RSS | 占比(2GB VPS) | 说明 |
|------|------|:---:|:-------------:|------|
| ml-factor | Fusion | **32 MB** | 1.6% | 因子层纯数学计算 |
| scheduler (ML) | ML | **95 MB** | 4.8% | LLM 定时分析调度 |
| monitor | ML | **245 MB** | 12.3% | WebSocket 盘中监控 |
| **合计** | | **372 MB** | **18.6%** | |

### 2.3 进程数量

| 类型 | 数量 | 说明 |
|------|:----:|------|
| daemon（常驻） | 4 | scheduler, monitor, ml-factor, realtime-fusion |
| oneshot（定时） | 8 | fusion, lynx-signal, TA, calibrate-alphas, eastmoney-rating, diagnose-agreement, trace-collect, alpha158 |

---

## 三、分发与部署建议

### 3.1 场景对照

| 场景 | 内存 | 磁盘 | 可运行？ | 说明 |
|------|:---:|:----:|:--------:|------|
| 开发者本地 (62GB) | ✅ | ✅ | ✅ 全功能 | 当前配置 |
| 4GB VPS | ⚠️ 3.6GB 可用 | ✅ 2.6GB | ✅ 全功能 | monitor 245MB 略高但可接受 |
| 2GB VPS | ⚠️ 1.6GB 可用 | ✅ 2.6GB | ✅ 谨慎 | 建议关闭 monitor 或降低监控频率 |
| 1GB VPS | ❌ 仅 0.6GB | ✅ | ❌ 不推荐 | 系统 + ML 依赖已占满 |

### 3.2 低配环境裁剪建议

```
关闭 monitor 服务:
  systemctl --user stop Aistock_vnpy_Trading-monitor.service
  systemctl --user disable Aistock_vnpy_Trading-monitor.service
  节省: 245MB RSS

关闭 realtime-fusion:
  systemctl --user stop Aistock_vnpy_Trading-realtime-fusion.service
  systemctl --user disable Aistock_vnpy_Trading-realtime-fusion.timer
  节省: 15MB RSS

保留核心功能:
  fusion (日终融合) + lynx-signal (量化信号) + eastmoney-rating (数据)
  = 约 50MB RSS (非 daemon, 执行完退出)
```

### 3.3 ML 推理性能说明

**本系统的 LLM 推理全部通过 DeepSeek API 云端完成**，本地不加载任何大模型。

| 组件 | 推理方式 | 性能依赖 | GPU 是否需要 |
|------|---------|---------|:-----------:|
| DeepSeek API 调用 | 云端 | 网络带宽 + API 响应时间 | ❌ |
| RandomForest (LY) | 本地 CPU | CPU 单核性能 | ❌ |
| 因子计算 (ML) | 本地 CPU | CPU 单核性能 | ❌ |

> 所有 LLM 调用走 API，本地不做推理。CPU-only 完全满足需求。
>
> 实际瓶颈不在 CPU：一次个股分析约 3-5 分钟，其中 80% 时间花在 API 等待和
> 数据获取，CPU 利用率通常低于 30%。

---

## 四、设计理念

> 以下是我对这个系统资源管理的一贯立场：

**1. 不过度优化**

372MB RSS 在 2026 年的硬件环境下不算什么。不值得为了省 100MB 去重写代码或砍功能。

**2. 可预测性优先于极致效率**

宁可进程多占 50MB，也比进程突然 OOM 被 kill 好。这也是为什么我们选择 Python（可预测的内存模型）而不是 Go/Rust。

**3. 分布式的目标是"可运行"，不是"高性能"**

如果要在 2GB VPS 上跑，我的建议是接受稍慢的速度，而不是裁剪功能。monitor 可以关，但 scheduler 和 fusion 不能关——因为那是系统的核心价值。

**4. 磁盘空间的真正敌人不是代码，是数据**

200MB 代码，2.3GB 依赖，但运行一个月后数据可能增长到 1GB+（回测数据库、全市场快照）。应对策略是定期清理（`data/research/` 已有归档机制），不是缩减代码。
