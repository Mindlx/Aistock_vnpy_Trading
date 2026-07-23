# 数据仓库服务层 — 实现文档

> 最后更新: 2026-07-06 (含盘前基本面刷新 10:50/13:50 + 数据湖兜底)
> 涉及提交: aa80fe2, f3542a6

---

## 一、背景与动机

### 1.1 问题: API 重复调用 + 反爬限流

三系统融合平台存在严重的数据重复获取问题：

```
LY 系统 ──→ Sina API     ──→ 拉OHLCV (每天1次)
ML 系统 ──→ akshare/EM   ──→ 拉OHLCV (每天4次整点+每5min)
AT 系统 ──→ akshare      ──→ 拉OHLCV+财务+资金流 (09:31/13:00各~80次)
EventMonitor ──→ CNINFO  ──→ 拉公告 (每5min)
```

**最严重窗口：13:00-13:05**
- TA.service 触发 ~80次 akshare 调用（8种数据 × 10只股票）
- EventMonitor 每300s一轮 ~30次 CNINFO/EM 调用
- 合计 **~110次/5分钟** 对东方财富系端点发起请求
- 东财反爬阈值约 ~200次/IP → 5-24h IP封禁

### 1.2 根因: 无统一数据管理层

三个子系统各自管理自己的数据获取，彼此不知晓对方已拉取过相同数据。每个系统独立限流、独立缓存、独立重试，导致：

| 问题 | 表现 |
|------|------|
| 数据重复获取 | 同一只股票的 OHLCV 被三个系统各自拉取 |
| 跨进程无协调 | TA 大量调用时不知 EventMonitor 也在调用 |
| 缓存碎片化 | LY 用 parquet, ML 用 SQLite, AT 无缓存 |
| 限流失效 | 各自限流独立, 总和远超 API 承受力 |

---

## 二、架构设计

### 2.1 核心理念: 数据服务层

在三个子系统下层插入一层透明的数据服务层：

```
                           ┌──────────────────┐
                           │  RefreshScheduler │ (唯一调API者)
                           │  15:30 OHLCV      │
                           │  16:00 财务       │
                           │  16:30 资金流     │
                           │  每小时 新闻      │
                           └────────┬─────────┘
                                    │ 写入
                                    ▼
┌────────────────────────────────────────────────────────┐
│              data_warehouse.db (SQLite WAL)              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐ │
│  │daily_ohlcv│ │realtime  │ │financial │ │news_events│ │
│  │          │ │_quotes   │ │_indicators│ │           │ │
│  ├──────────┤ ├──────────┤ ├──────────┤ ├───────────┤ │
│  │capital   │ │fundament-│ │cache_meta│ │rate_limit │ │
│  │_flows    │ │als       │ │          │ │_state     │ │
│  └──────────┘ └──────────┘ └──────────┘ └───────────┘ │
└────────────────────┬───────────────────────────────────┘
                     │ 读取 (WarehouseReader)
          ┌──────────┼──────────────────┐
          ▼          ▼                  ▼
       LY 系统    ML 系统           AT 系统
    (缓存优先)   (缓存优先)         (预热触发)
          ▲          ▲                  ▲
          └──────────┴──────────────────┘
               try/except ImportError
               仓库离线 → 自动回退原始API
```

### 2.2 设计原则

| 原则 | 实现 |
|------|------|
| **零侵入** | 所有集成点 try/except ImportError 保护，仓库不可用自动回退原始 API |
| **渐进迁移** | 每阶段独立可部署，阶段间互不依赖 |
| **数据一致性** | 统一 SQLite WAL 模式，支持 1 写 N 读并行 |
| **跨进程协调** | SQLite 原子事务实现令牌桶，所有进程共享同一个限流状态 |

---

## 三、模块详解

### 3.1 模块目录

```
services/data_warehouse/
├── __init__.py       # 公开 API 导出
├── config.py         # 配置中心: TTL矩阵, 限流参数, 股票池
├── storage.py        # SQLite 数据湖: 8 张表 + CRUD + 过期清理
├── limiter.py        # TokenBucketRateLimiter: 跨进程令牌桶
├── fetchers.py       # 数据获取器: 每种数据类型一个 Fetcher 类
├── warehouse.py      # WarehouseReader: 统一读取接口
├── scheduler.py      # RefreshScheduler: 定时刷新调度器
└── warmer.py         # DataWarmer: 首次部署预热脚本
```

总代码量: ~1300 行, 零外部依赖（仅复用项目已有的 akshare/efinance/httpx）。

### 3.2 config.py — 配置中心

所有可调参数集中在此, 通过 `DataWarehouseConfig.get_instance()` 单例读取:

```python
@dataclass
class DataWarehouseConfig:
    stock_pool: list[str]          # 股票池
    db_path: str                   # SQLite 路径 (默认 data/data_warehouse.db)
    rate_limits: dict              # 令牌桶参数
    data_types: dict               # 每种数据的 TTL/刷新策略
```

**令牌桶配置**:

| API 源 | 速率 | 桶容量 | 基础退避 |
|--------|------|--------|---------|
| eastmoney | 15/min | 15 | 1.0s |
| sina | 60/min | 60 | 2.0s |
| tencent | 120/min | 120 | 0.5s |
| cninfo | 30/min | 30 | 1.0s |
| tushare | 50/min | 50 | 1.0s |

**数据 TTL**:

| 数据类型 | TTL | 刷新触发 |
|---------|-----|---------|
| daily_ohlcv | 24h | 15:30 工作日 |
| realtime_quotes | 5min | 盘中每5分钟 |
| financial_indicators | 24h | 16:00 工作日 |
| capital_flows | 24h | 16:30 工作日 |
| news_events | 1h | 每小时 |
| fundamentals | 7d | 周一09:00 + **每日10:50/13:50**（盘前预热） |

### 3.3 limiter.py — 跨进程令牌桶

核心算法: 使用 SQLite `BEGIN IMMEDIATE` + 条件 `UPDATE` 实现多进程原子令牌消费。

```python
class TokenBucketLimiter:
    def consume(self, source: str, tokens=1.0, timeout=15.0) -> bool
    def retry(self, source, max_retries=3, base_delay=1.0, jitter=0.3) -> decorator
```

**工作流程**:
1. 读取 `rate_limit_state` 表的当前令牌数 + 最后补充时间
2. 计算应补充的令牌数: `new_tokens = min(max, current + (now - last) * rate)`
3. 如果足够: `UPDATE ... WHERE tokens=current` (CAS 原子操作), 成功则返回
4. 如果不够: 回滚, 计算等待时间, `sleep(≤50ms)` 后重试
5. 超时未获令牌 → `RateLimitError`

**使用方式**:
```python
limiter = TokenBucketLimiter()

@limiter.retry("eastmoney", max_retries=3)
def fetch_something(code):
    return ak.stock_zh_a_hist(...)
```

### 3.4 storage.py — SQLite 数据湖

8 张业务表 + 2 张元数据表, WAL 模式支持多进程并发:

| 表 | 主键 | 用途 |
|----|------|------|
| daily_ohlcv | (stock_code, date) | 日K线 OHLCV |
| realtime_quotes | (stock_code) | 实时行情快照 |
| financial_indicators | (stock_code, period, indicator_name) | 财务指标(多期) |
| capital_flows | (stock_code, date) | 资金流向 |
| news_events | (id, autoincrement) | 新闻/公告/事件 |
| fundamentals | (stock_code) | 基本面快照 |
| cache_meta | (stock_code, data_type) | 缓存元数据和TTL追踪 |
| rate_limit_state | (source) | 令牌桶状态(跨进程) |

### 3.5 fetchers.py — 数据获取器

每类数据一个 Fetcher, 内置多级降级链:

| Fetcher | 降级链 |
|---------|--------|
| DailyFetcher | akshare(EM) → akshare(Sina) → efinance |
| RealtimeFetcher | akshare(腾讯批量) → akshare(全市场快照) |
| FinancialFetcher | akshare(财务分析指标) |
| CapitalFlowFetcher | akshare(个股资金流向) |
| NewsFetcher | akshare(东财新闻) → CNINFO API(公告) |
| FundamentalsFetcher | akshare(个股信息+PE/PB+财务指标) |

所有 fetcher 方法通过 `@limiter.retry("source")` 装饰器受令牌桶保护。

### 3.6 warehouse.py — WarehouseReader

统一读取接口, 三阶段策略:

```
请求数据
  │
  ├─ 缓存命中 + 未过期 ──────→ 直接返回 (最快路径)
  │
  ├─ 缓存存在但已过期 ──────→ 返回旧数据 + stale=True
  │                           后台触发异步刷新
  │
  └─ 缓存不存在 ────────────→ 降级调用原始 API
                                写入缓存
                                返回
```

对外方法:

```python
class WarehouseReader:
    def get_daily(self, code, start="", end="", days=120) -> list[dict]
    def get_daily_df(self, code, ...) -> pd.DataFrame
    def get_realtime(self, code) -> dict | None
    def get_realtime_batch(self, codes) -> dict[str, dict]
    def get_financial(self, code) -> dict
    def get_pe_pb(self, code) -> dict
    def get_capital_flows(self, code, days=30) -> list[dict]
    def get_news(self, code="", days=7, min_importance=0, limit=50) -> list[dict]
    def get_fundamentals(self, code) -> dict | None
    def is_fresh(self, code, data_type) -> bool
    def invalidate(self, code, data_type=None)
    def prefetch_all(self, codes, data_types=None, force=False) -> dict
    def stats(self) -> dict
```

---

## 四、集成方式

### 4.1 LY 系统 (lynx_signal.py)

**函数**: `fetch_daily_bars()`
**改动**: 在检查本地 parquet 缓存之前插入 WarehouseReader 检查:
```python
try:
    from services.data_warehouse import WarehouseReader
    reader = WarehouseReader()
    if reader.is_fresh(code, "daily_ohlcv"):
        df = reader.get_daily_df(code, days=days)
        # 映射回中文列名, 写入 parquet 缓存, 返回
except ImportError:
    pass  # 仓库不可用 → 走原始 Sina API
```

### 4.2 ML 系统 (pipeline_data.py)

**方法**: `fetch_and_save_stock_data()`
**改动**: 在调用 `fetcher_manager.get_daily_data()` 之前插入 WarehouseReader:
```python
try:
    reader = WarehouseReader()
    if reader.is_fresh(code, "daily_ohlcv"):
        df = reader.get_daily_df(code, days=30)
        self.db.save_daily_data(df, code, "data_warehouse")
        return True, None
except ImportError:
    pass
```

### 4.3 AT 系统 (mind_agent_wrapper.py)

**方法**: `run_single()`
**改动**: 在分析前预热 WarehouseReader 缓存:
```python
try:
    reader = WarehouseReader()
    if not reader.is_fresh(code, "daily_ohlcv"):
        reader.get_daily(code, days=120)
except ImportError:
    pass
```

### 4.4 EventMonitor (event_monitor.py)

**方法**: `check_once()`
**改动**: 事件处理完毕后写入数据湖 news_events 表:
```python
try:
    lake = DataLake()
    news_items = [{
        "stock_code": ev.code, "title": ev.title,
        "source": ev.source, "importance": min(3, ev.importance // 3),
        ...
    } for ev in all_events]
    lake.insert_news(news_items)
except ImportError:
    pass
```

这样 EventMonitor 是**新闻生产者**, AT/ML 的分析流程通过 `WarehouseReader.get_news()` 直接读取, 不再需要重复请求 API。

---

## 五、部署

### 5.1 调度器守护进程

```bash
# 单次全量刷新 (预热)
python -m services.data_warehouse.scheduler --oneshot

# 守护进程模式
systemctl --user enable --now Aistock_vnpy_Trading-data-warehouse.service

# 查看状态
systemctl --user status Aistock_vnpy_Trading-data-warehouse.service

# 查看日志
journalctl --user -u Aistock_vnpy_Trading-data-warehouse.service -f
```

### 5.2 预热脚本

```bash
# 拉取所有股票1年历史数据
python -m services.data_warehouse.warmer

# 指定股票和回溯年数
python -m services.data_warehouse.warmer --codes 600519,000001 --years 2
```

### 5.3 手动填充缓存

如果东方财富源触发反爬导致预热失败，可以使用 Sina 接口直接填充：

```python
# 通过 LY 已验证的 Sina API 直连填充日K线缓存
cd ~/workspace/Aistock_vnpy_Trading
.venv/bin/python -c "
from services.data_warehouse.storage import DataLake
import requests, time, random

STOCKS = ['001390','300652','600372','605368','000592','603189','603557','688202','601801','300676','603127','000999','301293','301106','002230','000988','000060','605117']
session = requests.Session()
session.headers.update({'Referer': 'https://finance.sina.com.cn'})
lake = DataLake()

for i, code in enumerate(STOCKS):
    prefix = 'sh' if code.startswith(('6','5','9')) else 'sz'
    resp = session.get(
        'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData',
        params={'symbol': f'{prefix}{code}', 'scale': 240, 'ma': 'no', 'datalen': 365},
        timeout=15
    )
    rows = [{**d, 'date': d['day'].replace('-','')[:8], 'source': 'sina',
             'amount': 0.0, 'pct_chg': 0.0, 'turnover': 0.0} for d in resp.json() if d]
    if rows:
        lake.upsert_ohlcv(code, rows)
    time.sleep(2 + random.uniform(0, 1))
"
```

> **说明**: Sina API 比东方财富接口限流宽松得多，适合作为首次预热的数据源。其他数据类型（财务、资金流等）会由调度器每小时/每日通过受控刷新逐步填充。

### 5.3 systemd 服务配置

```ini
[Unit]
Description=Aistock Data Warehouse
After=network-online.target

[Service]
Type=simple
ExecStart=.../.venv/bin/python -m services.data_warehouse.scheduler --daemon
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
```

---

## 六、实施阶段

| 阶段 | 内容 | 文件 | 状态 |
|------|------|------|------|
| Phase 0 | 数据仓库模块 (7文件 ~1300行) | `services/data_warehouse/*` | ✅ 完成 |
| Phase 1 | LY 集成 | `lynx_signal.py: fetch_daily_bars()` | ✅ 完成 |
| Phase 2 | ML 集成 | `pipeline_data.py: fetch_and_save_stock_data()` | ✅ 完成 |
| Phase 3 | AT 集成 | `mind_agent_wrapper.py: run_single()` | ✅ 完成 |
| Phase 4 | systemd 守护进程 | `data-warehouse.service` + `scheduler.py --daemon` | ✅ 完成 |
| Phase 5 | EventMonitor → 数据湖链路 | `event_monitor.py: check_once()` → `news_events` 表 | ✅ 完成 |

---

## 七、预期收益

| 指标 | 优化前 | 优化后 | 说明 |
|------|--------|--------|------|
| 日K线 API 调用 | 每天 ~30次 (三系统独立拉取) | 每天 **1次** (收盘后统一批量) | 减 **97%** |
| 峰值 API 调用 (13:00) | ~110次/5分钟 | ~30次/5分钟 (仅 EventMonitor) | 减 **73%** |
| 东方财富封IP概率 | 高 (~200次触发) | 低 (15/min 令牌桶控制) | 大幅降低 |
| 数据一致性 | 三系统各自缓存, 可能不一致 | 统一数据湖, 一份数据 | 保证一致 |
| 子系统独立性 | 依赖各自数据源 | 不变, 仓库离线自动回退 | 完全保留 |
| 新系统接入成本 | 需要自行处理数据获取+限流 | 直接 `WarehouseReader.get_*()` 即可 | 大幅降低 |

---

## 八、风险与预案

| 风险 | 概率 | 缓解措施 |
|------|------|---------|
| SQLite 写锁竞争 | 低 | WAL 模式 + 10只股票规模无压力 |
| 令牌桶死锁 | 低 | `BEGIN IMMEDIATE` + 50ms 轮询超时 |
| 缓存数据过期 | 中 | TTL 保守配置 + `invalidate()` 手动失效 |
| 数据源全面不可用 | 低 | 原有 API 降级链仍然保留 |
| 守护进程崩溃 | 低 | systemd Restart=on-failure 自愈 |
