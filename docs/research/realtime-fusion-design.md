# 准实时融合架构设计 — v1（已取代）

> ⚠️ **此文档已被 v2 取代**。当前实现基于 `docs/decisions/realtime-fusion.md`（v2）。
> 本文保留仅用于历史参考。

> 让融合从 T+1（日终）升级为 15 分钟级（盘中）
> 设计日期: 2026-05-30

---

## 1. 设计目标

### 1.1 现状

```
ly (T+1 固定)  ─────  15:30 日终融合  ───→  企业微信推送
ml (日终报告)
at (昨日数据 stale)
```

### 1.2 目标

```
ly (T+1 固定)        ───┐
ml (15min 级更新)    ───┤──→  轻量融合服务  ──→  信号变化时推送
at (50min 级更新)    ───┘       ↑ 事件驱动
```

### 1.3 关键决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 融合触发方式 | **文件变化事件** (inotify) | 比轮询更及时、更省资源 |
| 数据传输 | **JSON 文件** | 零额外依赖，现有代码可直接读写 |
| 融合频率 | ~15 分钟（随 ml 节奏） | 与最快的信号源同步 |
| 推送条件 | L7 得分变化 > 0.3 | 避免噪声推送 |
| 日终融合 | **保留不变** | 作为权威版本，盘后推送 |

---

## 2. 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                   准实时融合系统                              │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ly (固定)           ml (15min)          at (50min)          │
│  ┌──────────┐      ┌────────────┐      ┌──────────┐        │
│  │ T+1 信号  │      │ 盘中速报    │      │ 多Agent  │        │
│  │ 不变     │      │ 趋势更新    │      │ 辩论结果  │        │
│  └────┬─────┘      └─────┬──────┘      └────┬─────┘        │
│       │                  │                   │              │
│       ▼                  ▼                   ▼              │
│  ┌───────────────────────────────────────────────────┐      │
│  │              data/realtime/ 文件交换区              │      │
│  │  ┌──────────────┐ ┌──────────┐ ┌──────────────┐  │      │
│  │  │ ly_signal    │ │ml_signal │ │ at_signal    │  │      │
│  │  │ .json (固定) │ │.json     │ │ .json        │  │      │
│  │  └──────────────┘ └──────────┘ └──────────────┘  │      │
│  └───────────────────────────────────────────────────┘      │
│                          │                                  │
│                          ▼                                  │
│  ┌───────────────────────────────────────────────────┐      │
│  │          轻量融合服务 (realtime_fusion.py)          │      │
│  │  ├─ 文件变化监听 (inotify)                         │      │
│  │  ├─ 融合计算 (ly×0.35 + ml×0.35 + at×0.30)       │      │
│  │  ├─ 变化检测 (|ΔL7| > 0.3 才推送)                 │      │
│  │  └─ 企业微信推送                                   │      │
│  └───────────────────────────────────────────────────┘      │
│                          │                                  │
│                          ▼                                  │
│              企业微信推送 (盘中实时信号)                      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 组件设计

### 3.1 文件交换区 `data/realtime/`

| 文件 | 写入者 | 更新频率 | 格式 |
|------|--------|---------|------|
| `ly_signal.json` | 日终融合运行后写入一次 | 每日 1 次 | `{"stocks": {"601801": {"score": 0.46, "prob_up": 57.6}, ...}}` |
| `ml_signal.json` | ml 盘中速报触发写入 | ~15 分钟 | `{"stocks": {"601801": {"advice": "持有", "score": 55, "timestamp": "2026-06-01T10:30:00"}, ...}}` |
| `at_signal.json` | at 每轮分析完成后写入 | ~50 分钟 | `{"stocks": {"601801": {"rating": "Buy", "l7_score": 2.3, "timestamp": "..."}, ...}}` |
| `fusion_result.json` | 轻量融合服务写入 | 每次融合后 | `{"stocks": {"601801": {"fusion_score": 0.85, "signal": "neutral", "timestamp": "..."}}, "pushed": true}` |

### 3.2 轻量融合服务 `src/realtime_fusion.py`

```python
class RealtimeFusion:
    """准实时融合服务 — 文件变化事件驱动"""

    def __init__(self):
        self.weights = {"lynx_vnpy": 0.35, "mindlynx": 0.35, "tradingagent": 0.30}
        self.last_scores = {}  # code → fusion_score (用于变化检测)
        self.watch_dir = Path("data/realtime/")
        self.watch_dir.mkdir(parents=True, exist_ok=True)

    def on_file_changed(self, path: Path):
        """文件变化回调"""
        signals = self._read_all_signals()
        for code in self._all_stock_codes(signals):
            fusion = self._fuse(code, signals)
            if self._should_push(code, fusion["score"]):
                self._push(code, fusion)
                self.last_scores[code] = fusion["score"]

    def _fuse(self, code: str, signals: dict) -> dict:
        ly = signals["ly"].get(code, {}).get("score", 0)
        ml = signals["ml"].get(code, {}).get("score", 0)
        at = signals["at"].get(code, {}).get("score", 0)
        score = ly*0.35 + ml*0.35 + at*0.30
        return {"score": score, "signal": self._to_label(score)}

    def _should_push(self, code: str, score: float) -> bool:
        """信号变化 > 0.3 L7 才推送"""
        last = self.last_scores.get(code, None)
        return last is None or abs(score - last) > 0.3
```

### 3.3 ml 信号输出（15 分钟级）

ml 的 scheduler daemon 已经周期性运行盘中分析。需要增加一个 hook：分析完成后写一份 JSON 到 `data/realtime/ml_signal.json`。

**方案**: 不修改 ml 代码，新增一个轻量 wrapper 脚本：

```bash
#!/bin/bash
# scripts/ml_realtime_hook.sh
# 在 ml scheduler 每次完成分析后调用
.venv/bin/python -c "
import json, sys
sys.path.insert(0, '.')
from src.data_loader import MindLynxDataLoader
from src.normalizer import SignalNormalizer as N

# 读取最新分析结果
ml = MindLynxDataLoader()
latest = ml.get_latest_available_date()
if latest:
    signals = ml.load_by_date(latest)
    # 转换为 L7 得分
    output = {}
    for code, s in signals.items():
        score = N.normalize_mindlynx(s['signal'], s['score'])
        output[code] = {'score': score, 'advice': s['signal'],
                        'timestamp': datetime.now().isoformat()}
    with open('data/realtime/ml_signal.json', 'w') as f:
        json.dump({'stocks': output}, f)
"
```

### 3.4 at 信号输出（50 分钟级）

修改 TA 定时器配置，增加日间运行：

```bash
# systemd timer 改为交易日多次触发
OnCalendar=Mon..Fri 10:00:00
OnCalendar=Mon..Fri 13:30:00
OnCalendar=Mon..Fri 14:30:00
```

`run_daily.py --run-ta` 运行完成后，自动写入 `data/realtime/at_signal.json`（已有 `run_batch_and_save()`，稍作改造即可）。

### 3.5 日终融合保持不变

15:30 的 `fusion.timer` 继续运行。它的结果作为**权威版本**输出到 `data/fusion_output/`。盘中融合的结果是轻量的、供参考的盘中信号。

### 3.6 企业微信推送格式

```
📊 盘中融合速报 - 06-01 10:45

⚠️ 信号变化:
  皖新传媒: 中性 → 谨慎看多 (+0.42 L7)
  华大基因: 中性 → 看空 (-0.68 L7)

📡 数据时效:
  ly: 昨日收盘 (T+1)
  ml: 3分钟前 (10:42)
  at: 12分钟前 (10:33)
```

---

## 4. 实施计划

### Phase 1 — 文件交换区 + 轻量融合 (Day 1)

| 任务 | 文件 | 工作量 |
|------|------|--------|
| 新建 `data/realtime/` 目录 | — | 5min |
| 新建 `src/realtime_fusion.py` | 核心服务 | 4h |
| ly 初始信号写入 | `run_daily.py` | 30min |
| ml 信号输出 hook | `scripts/ml_realtime_hook.sh` | 1h |
| at 信号输出改造 | `run_daily.py --run-ta` | 1h |

### Phase 2 — 定时器配置 (Day 2)

| 任务 | 文件 | 工作量 |
|------|------|--------|
| 新增 `realtime-fusion.service` | systemd | 30min |
| 新增 `realtime-fusion.timer` (每 5 分钟触发) | systemd | 30min |
| 修改 `TA.timer` 为日间多次触发 | systemd | 15min |
| 启用所有新服务 | — | 15min |

### Phase 3 — 测试与调优 (Day 3)

| 任务 | 内容 | 工作量 |
|------|------|--------|
| 单元测试 | `realtime_fusion.py` 各方法 | 2h |
| 模拟运行 | 用 mock 数据验证全流程 | 1h |
| 阈值调优 | 找到最佳推送变化阈值 | 1h |

---

## 5. 关键设计决策

| 决策 | 选项 A | 选项 B | 选择 |
|------|--------|--------|------|
| 触发方式 | 文件 inotify 监听 | 定时轮询 | **定时轮询** — 跨平台兼容，无需额外依赖 |
| 融合频率 | ~15 分钟 | ~5 分钟 | **每 5 分钟扫描一次** — 即使无新数据也不浪费资源 |
| ly 更新 | 仅日终 | 每次融合都计算 | **日终** — ly 盘中不变 |
| 盘中推送 | 每次都推 | 变化 > 阈值才推 | **变化 > 0.3 L7** — 避免噪声 |
| 历史记录 | 不保留 | 保留 | **保留当日 CSV** — 便于复盘 |

---

## 6. 风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| ml 盘中速报格式不稳定 | 解析失败 | 健壮解析 + 默认值降级 |
| at 日间多次运行消耗 API 额度 | 每次 ~100 次 LLM 调用 | 控制在 3-4 次/日，监控用量 |
| 频繁推送导致企业微信限流 | 消息丢失 | 变化阈值 + 合并推送 |
| 文件并发写入冲突 | 读取脏数据 | JSON 原子写入 (write+rename) |

---

## 7. 与现有系统的关系

```
现有系统                   新增组件
─────────                 ────────
fusion.timer (15:30)       realtime-fusion.timer (每5min)
TA.timer (16:00)           TA.timer (10:00, 13:30, 14:30)
monitor.service            不变
scheduler.service          不变

日终融合 → data/fusion_output/   权威版本
准实时融合 → data/realtime/       盘中参考
企业微信 → 两者都推               不同前缀区分
```
