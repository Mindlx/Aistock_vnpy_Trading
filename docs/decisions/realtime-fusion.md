# 准实时融合方案设计 v2.0

> 基于 c1skill 论证结论：ml 参与融合的价值在于因子层（客观数学），非 LLM 层。
> 设计日期: 2026-05-30

---

## 1. 核心架构

### 1.1 三系统的新定位

```
系统     数据时效      信号性质        融合角色        权重
────────────────────────────────────────────────────────────────
ly       T+1 (昨日)   数学模型(RF)     基线信号         0.30
ml-因子   实时(有新K线)  数学模型(12因子)  日内填补         0.40 ↑
at       ~50分钟      LLM辩论共识      主观判断         0.30
```

**关键变化**: ml 的融合权重从 0.35 提升至 **0.40**，因为它从"半 LLM 半数学"变为"纯数学"。两个数学模型共占 70%，LLM 观点占 30%。

### 1.2 数据流

```
                  ┌──────────────────────────────┐
                  │       stock_daily DB          │
                  │  (systems/MindLynx-Aistock/   │
                  │   data/stock_analysis.db)      │
                  └──────────┬───────────────────┘
                             │ 读最新OHLCV
                             ▼
┌──────────┐   ┌──────────────────────┐   ┌──────────┐
│  ly      │   │  ml_factor_service   │   │  at      │
│ (T+1固定)│   │  (每有新K线触发)      │   │ (50分钟) │
│          │   │                      │   │          │
│ 写入     │   │  FactorEngine        │   │  写入    │
│ ly_signal│   │  .compute_for_stock()│   │ at_signal│
│ .json    │   │  → composite_score   │   │ .json    │
└────┬─────┘   └──────────┬───────────┘   └────┬─────┘
     │                    │                    │
     ▼                    ▼                    ▼
  ┌──────────────────────────────────────────────────┐
  │          data/realtime/ 文件交换区                  │
  │  ly_signal.json (T+1固定)                         │
  │  ml_signal.json  (实时更新)                        │
  │  at_signal.json  (~50分钟更新)                     │
  └──────────────────────────────────────────────────┘
                             │
                             ▼
  ┌──────────────────────────────────────────────────┐
  │          src/realtime_fusion.py                   │
  │  轻量融合服务 (Type=simple, --daemon)              │
  │  ├─ 每 5 分钟扫描文件变化                          │
  │  ├─ fusion = ly×0.30 + ml_factor×0.40 + at×0.30  │
  │  └─ 变化 > 阈值时推送企业微信                       │
  └──────────────────────────────────────────────────┘
```

---

## 2. 组件详细设计

### 2.1 ml 因子服务 `services/ml_factor_service.py`

```python
"""
ml 因子服务 — 直接从 stock_daily DB 读取最新数据，
调用 FactorEngine 计算 12 因子 composite_score。
完全绕过 LLM 层，纯数学计算。

更新策略: 每 5 分钟检查一次 DB 是否有新数据
（stock_daily 由 ml scheduler 定时写入）
"""

import sqlite3
import json
import time
from pathlib import Path
from typing import Any, Dict

# 导入 ml 的因子引擎
ML_SYSTEM = Path("systems/MindLynx-Aistock")
FACTOR_ENGINE_PATH = str(ML_SYSTEM / "src")
DB_PATH = ML_SYSTEM / "data" / "stock_analysis.db"
OUTPUT_PATH = Path("data/realtime/ml_signal.json")

class MLFactorService:
    """ml 因子层服务 — 纯数学计算，无 LLM"""

    STOCK_CODES = ['001390','300652','600372','605368',
                   '000592','603189','603557','688202','601801','300676']

    def __init__(self):
        sys.path.insert(0, FACTOR_ENGINE_PATH)
        from src.core.factor_engine import FactorEngine
        self.engine = FactorEngine()
        self._db_conn = None

    @property
    def db(self):
        if self._db_conn is None:
            self._db_conn = sqlite3.connect(str(DB_PATH))
        return self._db_conn

    def compute_all(self) -> Dict[str, Any]:
        """计算所有股票的最新因子得分"""
        results = {}
        for code in self.STOCK_CODES:
            score = self._compute_one(code)
            if score is not None:
                results[code] = score
        return {"stocks": results, "timestamp": time.time()}

    def _compute_one(self, code: str) -> Dict[str, float]:
        """计算单只股票的因子 composite_score"""
        meta = self.db.execute('PRAGMA table_info(stock_daily)').fetchall()
        cols = [c[1] for c in meta]
        rows = self.db.execute(
            f"SELECT * FROM stock_daily WHERE code=? ORDER BY date", (code,)
        ).fetchall()
        if not rows:
            return None
        daily = [dict(zip(cols, r)) for r in rows]
        result = self.engine.compute_for_stock(code, daily)
        return {
            "composite_score": float(result.composite_score),
            "composite_label": result.composite_label,
            "factors": {k: float(v) for k, v in result.raw_factors.items()},
        }

    def run_once(self):
        """执行一次计算并写入文件"""
        data = self.compute_all()
        # 原子写入
        tmp = str(OUTPUT_PATH) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.rename(tmp, str(OUTPUT_PATH))
        return data

    def run_daemon(self, interval: int = 300):
        """daemon 模式: 每 interval 秒检查一次"""
        while True:
            self.run_once()
            time.sleep(interval)
```

### 2.2 轻量融合服务 `src/realtime_fusion.py`

```python
"""
准实时融合服务 — 文件交换区驱动，每 5 分钟扫描一次。
融合公式: score = ly×0.30 + ml_factor×0.40 + at×0.30
"""

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REALTIME_DIR = Path("data/realtime")
THRESHOLD_BY_ZONE = {
    "neutral": 0.3,     # 中性区 (±0.5): 变化 0.3 才推
    "border": 0.2,      # 临界区 (0.5~1.0): 变化 0.2 就推
    "directional": 0.5, # 方向区 (>1.0): 变化 0.5 才推
}

class RealtimeFusion:
    WEIGHTS = {"lynx": 0.30, "ml_factor": 0.40, "tradingagent": 0.30}

    def __init__(self):
        REALTIME_DIR.mkdir(parents=True, exist_ok=True)
        self._last_scores: Dict[str, float] = {}

    def scan_and_fuse(self) -> List[Dict[str, Any]]:
        """扫描文件交换区，融合所有股票"""
        signals = self._read_all()
        if not signals:
            return []

        # 确保 ly 基线存在（若缺失则日终融合生成）
        if "ly" not in signals:
            return []

        results = []
        for code in self._all_codes(signals):
            fusion = self._fuse_one(code, signals)
            if self._should_push(code, fusion["score"]):
                self._push(fusion)
                self._last_scores[code] = fusion["score"]
            results.append(fusion)
        return results

    def _fuse_one(self, code: str, signals: dict) -> dict:
        ly = signals.get("ly", {}).get(code, {}).get("score", 0)
        ml = signals.get("ml_factor", {}).get(code, {}).get("composite_score", 0)
        at = signals.get("at", {}).get(code, {}).get("score", 0)
        score = ly * 0.30 + ml * 0.40 + at * 0.30
        return {"code": code, "score": round(score, 3),
                "signal": self._to_label(score), "ly": ly, "ml": ml, "at": at}

    def _should_push(self, code: str, score: float) -> bool:
        last = self._last_scores.get(code)
        if last is None:
            return True  # 首次推送
        delta = abs(score - last)
        abs_score = abs(score)
        if abs_score < 0.5:
            return delta > THRESHOLD_BY_ZONE["neutral"]
        elif abs_score < 1.0:
            return delta > THRESHOLD_BY_ZONE["border"]
        else:
            return delta > THRESHOLD_BY_ZONE["directional"]
```

### 2.3 文件交换区设计

```
data/realtime/
├── ly_signal.json        # 写入: run_daily.py (日终), 更新频率: 每日1次
├── ml_signal.json        # 写入: ml_factor_service, 更新频率: 每5分钟
├── at_signal.json        # 写入: run_daily.py --run-ta, 更新频率: ~50分钟
├── fusion_result.json    # 写入: realtime_fusion, 更新频率: 每次融合后
└── fusion_history.csv    # 写入: realtime_fusion, 当日历史记录
```

JSON 格式规范:

```json
// ly_signal.json
{"stocks": {"601801": {"score": 0.46, "prob_up": 57.6, "signal": "观望"},
            "300652": {"score": -1.49, "prob_up": 25.0, "signal": "回避"}},
 "updated_at": "2026-06-01T15:30:00"}

// ml_signal.json  
{"stocks": {"601801": {"composite_score": 0.32, "label": "neutral",
                        "factors": {"momentum_reversal": 0.08, ...}}},
 "updated_at": "2026-06-01T10:30:00"}

// at_signal.json
{"stocks": {"601801": {"rating": "Buy", "score": 2.3}},
 "updated_at": "2026-06-01T10:30:00"}
```

### 2.4 ly 信号初始化

`run_daily.py` 日终融合运行后，增加一步：写入 `ly_signal.json`。

```python
# 在 run_daily.py 的 save_fusion_output() 之后追加
def save_ly_realtime_signal(results: List[Dict]):
    """将 ly 的当日信号写入准实时文件交换区"""
    ly_signals = {}
    for r in results:
        code = r["stock_code"]
        ly_signals[code] = {
            "score": r.get("lynx_score", 0),
            "prob_up": None,  # 从原始数据获取
        }
    data = {"stocks": ly_signals, "updated_at": datetime.now().isoformat()}
    tmp = "data/realtime/ly_signal.json.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.rename(tmp, "data/realtime/ly_signal.json")
```

---

## 3. systemd 配置

### 3.1 ml 因子服务

```ini
# /home/bluekuma/.config/systemd/user/Aistock_vnpy_Trading-ml-factor.service
[Unit]
Description=Aistock_vnpy_Trading — ml 因子层服务（实时计算，无 LLM）
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/bluekuma/workspace/Aistock_vnpy_Trading
ExecStart=/home/bluekuma/workspace/Aistock_vnpy_Trading/.venv/bin/python \
    services/ml_factor_service.py --daemon --interval 300
Restart=always
RestartSec=10
```

### 3.2 准实时融合服务

```ini
# /home/bluekuma/.config/systemd/user/Aistock_vnpy_Trading-realtime-fusion.service
[Unit]
Description=Aistock_vnpy_Trading — 准实时融合服务（文件交换驱动）
After=network-online.target Aistock_vnpy_Trading-ml-factor.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/bluekuma/workspace/Aistock_vnpy_Trading
ExecStart=/home/bluekuma/workspace/Aistock_vnpy_Trading/.venv/bin/python \
    src/realtime_fusion.py --daemon --interval 300
Restart=always
RestartSec=10
```

---

## 4. 与现有系统的关系

```
现有服务                              新增服务
────────                             ────────
fusion.timer (15:30) ─────────── 不变
  ├─ ly 预测 (T+1)               不变
  ├─ ml 报告读取 (日终)            保留（作为日终LLM参考）
  ├─ at 读取 (昨日stale)          不变
  └─ 输出 data/fusion_output/     不变
       └─ 增加: 写入 ly_signal.json ← 新增

TA.timer (16:00) ────────────── 不变

monitor.service ─────────────── 不变（风控事件独立推送）

                                  ml-factor.service (新增, 每5分钟)
                                    └─ 读 stock_daily DB → FactorEngine
                                        → 写入 ml_signal.json

                                  realtime-fusion.service (新增, 每5分钟扫描)
                                    ├─ 读 ly/ml/at signal → 融合
                                    └─ 变化 > 阈值 → 企业微信推送
```

---

## 5. 实施计划

### Phase 1 — 核心服务 (Day 1)

| 任务 | 文件 | 工作量 |
|------|------|--------|
| 新建 `services/ml_factor_service.py` | 因子层服务 | 3h |
| 新建 `src/realtime_fusion.py` | 轻量融合引擎 | 3h |
| 修改 `run_daily.py` | 日终写 ly_signal.json | 0.5h |
| 修改 `run_daily.py --run-ta` | TA 写 at_signal.json | 0.5h |

### Phase 2 — 部署与集成 (Day 2)

| 任务 | 文件 | 工作量 |
|------|------|--------|
| 新建 ml-factor systemd service | systemd | 0.5h |
| 新建 realtime-fusion systemd service | systemd | 0.5h |
| 创建 data/realtime/ 目录 + 初始化 | — | 0.2h |
| 启用服务并验证 | — | 0.5h |

### Phase 3 — 测试与调优 (Day 2-3)

| 任务 | 内容 | 工作量 |
|------|------|--------|
| 单元测试 | ml_factor_service + realtime_fusion | 2h |
| 模拟运行 | 用 mock 数据验证全流程 | 1h |
| 阈值调优 | 确定推送变化阈值 | 1h |
| 企业微信推送格式设计 | 消息模板 | 0.5h |
| **合计** | | **~12h** |

---

## 6. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| stock_daily DB 被 ml scheduler 写入时有锁 | 中 | 中 | SQLite WAL 模式允许多读一写，因子服务只读不影响 |
| ml 因子数据不满足 5 分钟粒度 | 低 | 中 | stock_daily 是日线数据，盘中可能无新行；改为监测"最后一条数据的日期时间" |
| at 日间多次运行 API 超时 | 中 | 低 | at_signal.json 缺失时融合用 0 分，不影响其他系统 |
| 盘中融合推送与日终推送冲突 | 低 | 低 | 消息前缀区分："盘中参考" vs "日终确认" |
